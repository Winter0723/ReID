#include "SearchClient.h"
#include <QUrlQuery>
#include <QHttpMultiPart>
#include <QHttpPart>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QTimer>
#include <QStringList>
#include <QUrl>
#include <QDateTime>
#include <QDir>
#include <QSaveFile>
#include <QStandardPaths>
#include <QtGlobal>

namespace {
constexpr int StatusPollIntervalMs = 3000;
constexpr auto AccessTokenKey = "accessToken";
constexpr auto RefreshTokenKey = "refreshToken";

QString apiErrorMessage(QNetworkReply *reply, const QString &fallback)
{
    const QByteArray response = reply->readAll();
    const QJsonDocument doc = QJsonDocument::fromJson(response);
    const QJsonValue detail = doc.object().value("detail");
    if (detail.isString()) {
        return detail.toString();
    }
    if (detail.isArray()) {
        QStringList messages;
        const QJsonArray detailItems = detail.toArray();
        for (const QJsonValue &item : detailItems) {
            const QJsonObject obj = item.toObject();
            const QString message = obj.value("msg").toString();
            if (!message.isEmpty()) {
                messages.append(message);
            }
        }
        if (!messages.isEmpty()) {
            return messages.join("; ");
        }
    }
    if (!response.isEmpty()) {
        return QString::fromUtf8(response);
    }
    return fallback + ": " + reply->errorString();
}

QString pathComponent(const QString &value)
{
    return QString::fromUtf8(QUrl::toPercentEncoding(value));
}

QString normalizedBaseUrl(QString value)
{
    value = value.trimmed();
    if (value.endsWith('/')) {
        value.chop(1);
    }
    return value.isEmpty() ? QStringLiteral("http://127.0.0.1:2889") : value;
}

QString sanitizedFileName(QString value)
{
    value = QFileInfo(value.trimmed()).fileName();
    const QString invalidChars = QStringLiteral("\\/:*?\"<>|");
    for (const QChar ch : invalidChars) {
        value.replace(ch, QLatin1Char('_'));
    }
    value = value.trimmed();
    if (value.isEmpty()) {
        value = QStringLiteral("myreid_media");
    }
    return value.left(180);
}

QString fileNameFromContentDisposition(const QByteArray &header)
{
    const QString value = QString::fromUtf8(header);
    const int marker = value.indexOf(QStringLiteral("filename="), 0, Qt::CaseInsensitive);
    if (marker < 0) {
        return {};
    }
    QString fileName = value.mid(marker + 9).trimmed();
    if (fileName.startsWith('"')) {
        const int endQuote = fileName.indexOf('"', 1);
        if (endQuote > 0) {
            fileName = fileName.mid(1, endQuote - 1);
        }
    } else {
        const int end = fileName.indexOf(';');
        if (end >= 0) {
            fileName = fileName.left(end);
        }
    }
    return fileName.trimmed();
}

QString uniqueDownloadPath(const QString &directoryPath, const QString &fileName)
{
    const QString safeName = sanitizedFileName(fileName);
    QFileInfo info(safeName);
    const QString baseName = info.completeBaseName().isEmpty() ? QStringLiteral("myreid_media") : info.completeBaseName();
    const QString suffix = info.suffix();
    QDir directory(directoryPath);
    QString candidate = directory.filePath(safeName);
    for (int index = 1; QFileInfo::exists(candidate) && index < 1000; ++index) {
        const QString numbered = suffix.isEmpty()
            ? QStringLiteral("%1_%2").arg(baseName).arg(index)
            : QStringLiteral("%1_%2.%3").arg(baseName).arg(index).arg(suffix);
        candidate = directory.filePath(numbered);
    }
    return candidate;
}

QString defaultDownloadDirectory()
{
    QString downloadDir = QStandardPaths::writableLocation(QStandardPaths::DownloadLocation);
    if (downloadDir.isEmpty()) {
        downloadDir = QDir::homePath();
    }
    return downloadDir;
}

QString localPathFromDialogSelection(QString value, const QString &suggestedName)
{
    value = value.trimmed();
    if (value.isEmpty()) {
        return {};
    }

    const QUrl url(value);
    QString path;
    if (url.isValid() && url.isLocalFile()) {
        path = url.toLocalFile();
    } else if (url.scheme().isEmpty()) {
        path = value;
    } else {
        return {};
    }

    const QString suggestedSuffix = QFileInfo(sanitizedFileName(suggestedName)).suffix();
    if (!suggestedSuffix.isEmpty() && QFileInfo(path).suffix().isEmpty()) {
        path += "." + suggestedSuffix;
    }
    return path;
}

void persistSecret(QSettings &settings, const QString &key, const QString &value)
{
    if (value.isEmpty()) {
        settings.remove(key);
        return;
    }
    settings.setValue(key, value);
}

QString restoreSecret(QSettings &settings, const QString &key)
{
    return settings.value(key).toString();
}

void clearSecret(QSettings &settings, const QString &key)
{
    settings.remove(key);
}
}

SearchClient::SearchClient(QObject *parent) : SearchClient("core", parent)
{
}

SearchClient::SearchClient(const QString &systemScope, QObject *parent) : QObject(parent)
{
    m_systemScope = systemScope.trimmed().isEmpty() ? "core" : systemScope.trimmed();
    QSettings settings("MyReID", "SearchClient");
    m_baseUrl = normalizedBaseUrl(qEnvironmentVariable("MYREID_API_BASE_URL", settings.value("baseUrl", m_baseUrl).toString()));
    m_manager = new QNetworkAccessManager(this);
    m_statusTimer = new QTimer(this);
    m_statusTimer->setInterval(StatusPollIntervalMs);
    m_statusTimer->setTimerType(Qt::CoarseTimer);
    connect(m_statusTimer, &QTimer::timeout, this, &SearchClient::pollStatus);
    m_cameraTimer = new QTimer(this);
    m_cameraTimer->setInterval(1500);
    m_cameraTimer->setTimerType(Qt::CoarseTimer);
    connect(m_cameraTimer, &QTimer::timeout, this, &SearchClient::fetchCameraStatus);

    // 启动时自动生成一个唯一的访客 UUID
    m_guestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    restoreSession();
    checkEnterpriseReadiness();
}

SearchClient::~SearchClient()
{
    m_statusTimer->stop();
    m_cameraTimer->stop();
    const auto replies = m_manager->findChildren<QNetworkReply *>();
    for (QNetworkReply *reply : replies) {
        if (reply && reply->isRunning()) {
            reply->abort();
        }
    }
}

bool SearchClient::isScenicScope() const
{
    return m_systemScope == QStringLiteral("scenic");
}

QString SearchClient::settingsPrefix() const
{
    return isScenicScope() ? QStringLiteral("scenic/") : QStringLiteral("core/");
}

QString SearchClient::apiPath(const QString &path) const
{
    if (!isScenicScope()) {
        return path;
    }
    if (path.startsWith(QStringLiteral("/api/v1/scenic/"))) {
        return path;
    }
    if (path == QStringLiteral("/api/v1/cameras")) {
        return QStringLiteral("/api/v1/scenic/cameras");
    }
    if (path == QStringLiteral("/api/v1/history")) {
        return QStringLiteral("/api/v1/scenic/history");
    }
    if (path.startsWith(QStringLiteral("/api/v1/history/"))) {
        return QStringLiteral("/api/v1/scenic/history/") + path.mid(QStringLiteral("/api/v1/history/").size());
    }
    if (path.startsWith(QStringLiteral("/api/v1/jobs/"))) {
        return QStringLiteral("/api/v1/scenic/jobs/") + path.mid(QStringLiteral("/api/v1/jobs/").size());
    }
    if (path.startsWith(QStringLiteral("/api/v1/auth/"))) {
        return QStringLiteral("/api/v1/scenic/auth/") + path.mid(QStringLiteral("/api/v1/auth/").size());
    }
    if (path == QStringLiteral("/api/v1/projects")) {
        return QStringLiteral("/api/v1/scenic/projects");
    }
    if (path.startsWith(QStringLiteral("/api/v1/tourists/"))) {
        return QStringLiteral("/api/v1/scenic/tourists/") + path.mid(QStringLiteral("/api/v1/tourists/").size());
    }
    if (!path.startsWith(QStringLiteral("/api/v1/projects/"))) {
        return path;
    }

    QString rest = path.mid(QStringLiteral("/api/v1/projects/").size());
    const int slash = rest.indexOf(QLatin1Char('/'));
    if (slash < 0) {
        return QStringLiteral("/api/v1/scenic/projects/") + rest;
    }
    const QString projectId = rest.left(slash);
    QString suffix = rest.mid(slash + 1);
    if (suffix.startsWith(QStringLiteral("scenic/"))) {
        suffix = suffix.mid(QStringLiteral("scenic/").size());
    }
    return QStringLiteral("/api/v1/scenic/projects/") + projectId + QLatin1Char('/') + suffix;
}

void SearchClient::setBackendBaseUrl(const QString &baseUrl)
{
    const QString normalized = normalizedBaseUrl(baseUrl);
    if (normalized == m_baseUrl) {
        return;
    }
    // Access tokens are audience-specific. Never replay a session to a newly
    // configured backend origin.
    clearLocalSessionState();
    m_baseUrl = normalized;
    QSettings settings("MyReID", "SearchClient");
    settings.setValue("baseUrl", m_baseUrl);
    emit backendBaseUrlChanged();
    checkEnterpriseReadiness();
}

void SearchClient::setupAuthHeaders(QNetworkRequest &request) {
    if (!m_token.isEmpty()) {
        request.setRawHeader("Authorization", ("Bearer " + m_token).toUtf8());
    }
}

QNetworkRequest SearchClient::jsonRequest(const QString &path)
{
    QNetworkRequest request(QUrl(m_baseUrl + apiPath(path)));
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    setupAuthHeaders(request);
    return request;
}

QNetworkRequest SearchClient::authRequest(const QString &path)
{
    QNetworkRequest request(QUrl(m_baseUrl + apiPath(path)));
    setupAuthHeaders(request);
    return request;
}

void SearchClient::persistSession()
{
    QSettings settings("MyReID", "SearchClient");
    const QString prefix = settingsPrefix();
    persistSecret(settings, prefix + AccessTokenKey, m_token);
    persistSecret(settings, prefix + RefreshTokenKey, m_refreshToken);
    settings.setValue("baseUrl", m_baseUrl);
    settings.setValue(prefix + "currentUser", m_currentUser);
    settings.setValue(prefix + "currentUserId", m_currentUserId);
    settings.setValue(prefix + "currentEmail", m_currentEmail);
    settings.setValue(prefix + "currentAvatarUrl", m_currentAvatarUrl);
    settings.setValue(prefix + "currentRole", m_currentRole);
    settings.setValue(prefix + "currentCreatedAt", m_currentCreatedAt);
    settings.setValue(prefix + "currentProjectId", m_currentProjectId);
    settings.setValue(prefix + "currentProjectName", m_currentProjectName);
    settings.setValue(prefix + "currentProjectType", m_currentProjectType);
    settings.setValue(prefix + "cameraSource", m_cameraSource);
}

void SearchClient::clearPersistedSession()
{
    QSettings settings("MyReID", "SearchClient");
    const QString prefix = settingsPrefix();
    clearSecret(settings, prefix + AccessTokenKey);
    clearSecret(settings, prefix + RefreshTokenKey);
    settings.remove(prefix + "currentUser");
    settings.remove(prefix + "currentUserId");
    settings.remove(prefix + "currentEmail");
    settings.remove(prefix + "currentAvatarUrl");
    settings.remove(prefix + "currentRole");
    settings.remove(prefix + "currentCreatedAt");
    settings.remove(prefix + "currentProjectId");
    settings.remove(prefix + "currentProjectName");
    settings.remove(prefix + "currentProjectType");
    settings.remove(prefix + "cameraSource");
}

void SearchClient::clearLocalSessionState()
{
    ++m_authGeneration;
    ++m_projectGeneration;
    ++m_touristSelectionGeneration;
    ++m_searchGeneration;
    const auto replies = m_manager->findChildren<QNetworkReply *>();
    for (QNetworkReply *reply : replies) {
        if (reply && reply->isRunning()) {
            reply->abort();
        }
    }
    m_refreshReply.clear();
    m_statusReply.clear();
    m_cameraReply.clear();
    m_readinessReply.clear();
    m_searchReply.clear();
    m_scenicMonitoringReply.clear();
    m_token.clear();
    m_refreshToken.clear();
    m_currentUser.clear();
    m_currentUserId.clear();
    m_currentEmail.clear();
    m_currentAvatarUrl.clear();
    m_currentRole.clear();
    m_currentCreatedAt.clear();
    m_currentProjectId = 0;
    m_currentProjectName.clear();
    m_currentProjectType = isScenicScope() ? "scenic" : "media";
    m_currentSessionId.clear();
    m_historyList.clear();
    m_previewList.clear();
    m_projectList.clear();
    m_mediaList.clear();
    m_jobList.clear();
    m_auditList.clear();
    m_adminUserList.clear();
    m_availableCameraList.clear();
    m_scenicDashboard.clear();
    m_scenicCameraPointList.clear();
    m_scenicCameraWall.clear();
    m_touristSessionList.clear();
    m_selectedTouristSession.clear();
    m_searchResultDetail.clear();
    m_searchPage = 1;
    m_searchPageSize = 12;
    m_searchTotal = 0;
    m_searchTotalPages = 0;
    m_searchHasPrevious = false;
    m_searchHasNext = false;
    m_lastSearchTaskId = 0;
    m_autoCreateDefaultProject = false;
    m_defaultProjectCreateInFlight = false;
    m_scenicMonitoringStartedProjectId = 0;
    m_scenicMonitoringStartInFlight = false;
    clearCameraStatus();
    clearPersistedSession();
    m_guestId = QUuid::createUuid().toString(QUuid::WithoutBraces);
    emit loginStateChanged();
    emit userProfileChanged();
    emit currentProjectChanged();
    emit sessionChanged();
    emit historyListChanged();
    emit previewListChanged();
    emit projectListChanged();
    emit mediaListChanged();
    emit jobListChanged();
    emit auditListChanged();
    emit adminUserListChanged();
    emit availableCameraListChanged();
    emit scenicDashboardChanged();
    emit scenicCameraPointListChanged();
    emit scenicCameraWallChanged();
    emit touristSessionListChanged();
    emit selectedTouristSessionChanged();
    emit searchPaginationChanged();
    emit searchResultDetailChanged();
}

void SearchClient::restoreSession()
{
    QSettings settings("MyReID", "SearchClient");
    const QString prefix = settingsPrefix();
    m_token = restoreSecret(settings, prefix + AccessTokenKey);
    m_refreshToken = restoreSecret(settings, prefix + RefreshTokenKey);
    m_currentUser = settings.value(prefix + "currentUser").toString();
    m_currentUserId = settings.value(prefix + "currentUserId").toString();
    m_currentEmail = settings.value(prefix + "currentEmail").toString();
    m_currentAvatarUrl = settings.value(prefix + "currentAvatarUrl").toString();
    m_currentRole = settings.value(prefix + "currentRole").toString();
    m_currentCreatedAt = settings.value(prefix + "currentCreatedAt").toString();
    m_currentProjectId = settings.value(prefix + "currentProjectId", 0).toInt();
    m_currentProjectName = settings.value(prefix + "currentProjectName").toString();
    m_currentProjectType = settings.value(prefix + "currentProjectType", isScenicScope() ? "scenic" : "media").toString();
    m_cameraSource = settings.value(prefix + "cameraSource", "0").toString();
    if (m_currentProjectId > 0) {
        m_currentSessionId = QString::number(m_currentProjectId);
    }
    emit loginStateChanged();
    emit userProfileChanged();
    emit currentProjectChanged();
    emit sessionChanged();

    if (m_token.isEmpty()) {
        if (!isScenicScope()) {
            createNewSession();
        }
        return;
    }
    fetchUserProfile();
    fetchProjects();
    fetchCameras();
    refreshWorkspace();
}

void SearchClient::setCurrentProject(const QJsonObject &project)
{
    const int nextProjectId = project["id"].toInt();
    if (nextProjectId != m_currentProjectId) {
        ++m_projectGeneration;
        if (m_statusReply) m_statusReply->abort();
        if (m_cameraReply) m_cameraReply->abort();
        if (m_scenicMonitoringReply) m_scenicMonitoringReply->abort();
        m_scenicMonitoringReply.clear();
        m_scenicMonitoringStartInFlight = false;
        m_scenicMonitoringStartedProjectId = 0;
    }
    m_currentProjectId = nextProjectId;
    m_currentProjectName = project["name"].toString();
    m_currentProjectType = project["project_type"].toString("media");
    const QString projectCameraSource = project["camera_source"].toString();
    m_cameraSource = projectCameraSource.isEmpty() ? "0" : projectCameraSource;
    m_currentSessionId = QString::number(m_currentProjectId);
    if (m_currentProjectType != "camera") {
        clearCameraStatus();
    }
    persistSession();
    emit currentProjectChanged();
    emit sessionChanged();
}

void SearchClient::updateUserProfile(const QJsonObject &profile)
{
    m_currentUserId = profile["id"].toString();
    m_currentUser = profile["username"].toString(m_currentUser);
    m_currentEmail = profile["email"].toString();
    const QString avatarUrl = profile["avatar_url"].toString();
    m_currentAvatarUrl = avatarUrl.startsWith("/") ? (m_baseUrl + avatarUrl) : avatarUrl;
    m_currentRole = profile["role"].toString();
    m_currentCreatedAt = profile["created_at"].toString();
    persistSession();
    emit loginStateChanged();
    emit userProfileChanged();
    if (!isScenicScope() && m_currentRole == "admin") {
        fetchAdminUsers();
    } else if (!m_adminUserList.isEmpty()) {
        m_adminUserList.clear();
        emit adminUserListChanged();
    }
}

void SearchClient::refreshAccessToken()
{
    if (m_refreshReply && m_refreshReply->isRunning()) {
        return;
    }
    if (m_refreshToken.isEmpty()) {
        logout();
        return;
    }

    const quint64 authGeneration = m_authGeneration;
    const QString refreshToken = m_refreshToken;
    QNetworkRequest request(QUrl(m_baseUrl + apiPath("/api/v1/auth/refresh")));
    request.setRawHeader("Authorization", ("Bearer " + refreshToken).toUtf8());
    QNetworkReply *reply = m_manager->post(request, QByteArray());
    m_refreshReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply, authGeneration, refreshToken]() {
        if (m_refreshReply == reply) m_refreshReply.clear();
        reply->deleteLater();
        if (authGeneration != m_authGeneration || refreshToken != m_refreshToken) {
            return;
        }
        if (reply->error() != QNetworkReply::NoError) {
            clearLocalSessionState();
            emit errorOccurred("登录已过期，请重新登录");
            return;
        }
        QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_token = obj["access_token"].toString();
        m_refreshToken = obj["refresh_token"].toString();
        persistSession();
        fetchUserProfile();
        fetchProjects();
    });
}

void SearchClient::registerUser(const QString &username, const QString &email, const QString &password, const QString &code)
{
    if (code.trimmed().isEmpty()) {
        emit authFinished(false, "请输入邮箱验证码");
        return;
    }
    const quint64 authGeneration = ++m_authGeneration;
    if (m_refreshReply) m_refreshReply->abort();

    QUrl url(m_baseUrl + apiPath("/api/v1/auth/register"));
    QNetworkRequest request(url);
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");

    QJsonObject json;
    json["username"] = username.trimmed();
    json["email"] = email.trimmed().toLower();
    json["password"] = password;
    json["code"] = code.trimmed();

    QNetworkReply *reply = m_manager->post(request, QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, username, email, reply, authGeneration]() {
        reply->deleteLater();
        if (authGeneration != m_authGeneration) return;
        if (reply->error() == QNetworkReply::NoError) {
            QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
            m_token = obj["access_token"].toString();
            m_refreshToken = obj["refresh_token"].toString();
            m_currentUser = username.trimmed();
            m_currentEmail = email.trimmed().toLower();
            persistSession();
            emit loginStateChanged();
            emit userProfileChanged();
            emit authFinished(true, "登录成功");
            fetchUserProfile();
            ensureDefaultProject();
        } else {
            emit authFinished(false, apiErrorMessage(reply, "注册失败"));
        }
    });
}

void SearchClient::requestEmailCode(const QString &email, const QString &purpose, const QString &username)
{
    const QString normalizedEmail = email.trimmed().toLower();
    if (normalizedEmail.isEmpty()) {
        emit emailCodeRequested(false, "请输入邮箱");
        return;
    }

    QJsonObject json;
    json["email"] = normalizedEmail;
    json["purpose"] = purpose.isEmpty() ? "login" : purpose;
    const QString normalizedUsername = username.trimmed();
    if (!normalizedUsername.isEmpty()) {
        json["username"] = normalizedUsername;
    }
    QNetworkReply *reply = m_manager->post(jsonRequest("/api/v1/auth/email-code/request"), QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit emailCodeRequested(false, apiErrorMessage(reply, "验证码发送失败"));
            return;
        }
        QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        QString message = obj["message"].toString("验证码已发送，请查收邮箱");
        const QString debugCode = obj["debug_code"].toString();
        if (!debugCode.isEmpty()) {
            message += "：" + debugCode;
        }
        emit emailCodeRequested(true, message);
    });
}

void SearchClient::loginWithEmailCode(const QString &email, const QString &code)
{
    const QString normalizedEmail = email.trimmed().toLower();
    if (normalizedEmail.isEmpty() || code.trimmed().isEmpty()) {
        emit authFinished(false, "请输入邮箱和验证码");
        return;
    }
    const quint64 authGeneration = ++m_authGeneration;
    if (m_refreshReply) m_refreshReply->abort();

    QJsonObject json;
    json["email"] = normalizedEmail;
    json["code"] = code.trimmed();

    QNetworkReply *reply = m_manager->post(jsonRequest("/api/v1/auth/email-code/login"), QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, normalizedEmail, reply, authGeneration]() {
        reply->deleteLater();
        if (authGeneration != m_authGeneration) return;
        if (reply->error() == QNetworkReply::NoError) {
            QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
            m_token = obj["access_token"].toString();
            m_refreshToken = obj["refresh_token"].toString();
            m_currentEmail = normalizedEmail;
            persistSession();
            emit loginStateChanged();
            emit authFinished(true, "登录成功");
            fetchUserProfile();
            ensureDefaultProject();
        } else {
            emit authFinished(false, apiErrorMessage(reply, "验证码登录失败"));
        }
    });
}

void SearchClient::createNewSession() {
    if (!m_token.isEmpty()) {
        ensureDefaultProject();
        return;
    }
    m_currentSessionId.clear();
    m_previewList.clear();
    emit previewListChanged();
    emit sessionChanged();
    checkInitialStatus();
}

void SearchClient::switchSession(const QString &sessionId) {
    if (m_currentSessionId != sessionId) {
        ++m_projectGeneration;
        if (m_statusReply) m_statusReply->abort();
        if (m_cameraReply) m_cameraReply->abort();
        if (m_scenicMonitoringReply) m_scenicMonitoringReply->abort();
        m_scenicMonitoringReply.clear();
        m_scenicMonitoringStartInFlight = false;
        m_scenicMonitoringStartedProjectId = 0;
        m_currentSessionId = sessionId;
        m_currentProjectId = sessionId.toInt();
        for (const QVariant &projectVar : m_projectList) {
            QVariantMap project = projectVar.toMap();
            if (project.value("id").toInt() == m_currentProjectId) {
                m_currentProjectName = project.value("name").toString();
                m_currentProjectType = project.value("project_type", "media").toString();
                const QString projectCameraSource = project.value("camera_source", "0").toString();
                m_cameraSource = projectCameraSource.isEmpty() ? "0" : projectCameraSource;
                break;
            }
        }
        if (m_currentProjectType != "camera") {
            clearCameraStatus();
        }
        persistSession();
        emit currentProjectChanged();
        emit sessionChanged();
        refreshWorkspace();
    }
}

void SearchClient::selectProject(int projectId)
{
    switchSession(QString::number(projectId));
}

void SearchClient::fetchPreview() {
    if (m_currentProjectId <= 0) {
        m_previewList.clear();
        emit previewListChanged();
        return;
    }
    QUrl url(m_baseUrl + apiPath("/api/v1/projects/" + QString::number(m_currentProjectId) + "/preview"));
    QNetworkRequest request(url);
    setupAuthHeaders(request);
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            QJsonArray arr = QJsonDocument::fromJson(reply->readAll()).object()["previews"].toArray();
            m_previewList.clear();
            for (const QJsonValue &val : arr) m_previewList.append(m_baseUrl + val.toString());
            emit previewListChanged();
        }
    });
}

void SearchClient::deleteHistory(int historyId) {
    QUrl url(m_baseUrl + apiPath("/api/v1/history/" + QString::number(historyId)));
    QNetworkRequest request(url);
    setupAuthHeaders(request);
    QNetworkReply *reply = m_manager->deleteResource(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        fetchHistory(); // 删完后刷新列表
    });
}
void SearchClient::login(const QString &account, const QString &password)
{
    const quint64 authGeneration = ++m_authGeneration;
    if (m_refreshReply) m_refreshReply->abort();
    QUrl url(m_baseUrl + apiPath("/api/v1/auth/login"));
    QNetworkRequest request(url);
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");

    QJsonObject json;
    json["username"] = account.trimmed();
    json["password"] = password;

    QNetworkReply *reply = m_manager->post(request, QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, account, reply, authGeneration]() {
        reply->deleteLater();
        if (authGeneration != m_authGeneration) return;
        if (reply->error() == QNetworkReply::NoError) {
            QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
            m_token = obj["access_token"].toString();
            m_refreshToken = obj["refresh_token"].toString();
            m_currentUser = account.trimmed();
            persistSession();
            emit loginStateChanged();
            emit authFinished(true, "登录成功");
            fetchUserProfile();
            ensureDefaultProject();
        } else {
            emit authFinished(false, apiErrorMessage(reply, "用户名或密码错误"));
        }
    });
}

void SearchClient::logout()
{
    const QString refreshToken = m_refreshToken;
    clearLocalSessionState();
    if (!refreshToken.isEmpty()) {
        QNetworkRequest request(QUrl(m_baseUrl + apiPath("/api/v1/auth/logout")));
        request.setRawHeader("Authorization", ("Bearer " + refreshToken).toUtf8());
        QNetworkReply *reply = m_manager->post(request, QByteArray());
        connect(reply, &QNetworkReply::finished, reply, &QNetworkReply::deleteLater);
    }
    checkInitialStatus();
}

void SearchClient::fetchUserProfile()
{
    if (m_token.isEmpty()) {
        return;
    }
    QNetworkReply *reply = m_manager->get(authRequest("/api/v1/auth/me"));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            if (reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt() == 401) {
                refreshAccessToken();
            }
            return;
        }
        updateUserProfile(QJsonDocument::fromJson(reply->readAll()).object());
    });
}

void SearchClient::uploadAvatar(const QString &fileUrl)
{
    if (m_token.isEmpty()) {
        emit profileUpdateFinished(false, "请先登录");
        return;
    }

    QString localFilePath = QUrl(fileUrl).toLocalFile();
    QFile *file = new QFile(localFilePath);
    if (!file->open(QIODevice::ReadOnly)) {
        emit profileUpdateFinished(false, "无法打开头像文件");
        delete file;
        return;
    }

    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart avatarPart;
    avatarPart.setHeader(QNetworkRequest::ContentTypeHeader, QVariant("image/jpeg"));
    avatarPart.setHeader(
        QNetworkRequest::ContentDispositionHeader,
        QVariant(QString("form-data; name=\"file\"; filename=\"%1\"").arg(QFileInfo(*file).fileName()))
    );
    avatarPart.setBodyDevice(file);
    file->setParent(multiPart);
    multiPart->append(avatarPart);

    QNetworkReply *reply = m_manager->post(authRequest("/api/v1/auth/avatar"), multiPart);
    multiPart->setParent(reply);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit profileUpdateFinished(false, apiErrorMessage(reply, "头像上传失败"));
            return;
        }
        updateUserProfile(QJsonDocument::fromJson(reply->readAll()).object());
        emit profileUpdateFinished(true, "头像已更新");
    });
}

void SearchClient::changePassword(const QString &currentPassword, const QString &newPassword)
{
    if (m_token.isEmpty()) {
        emit userOperationFinished(false, "请先登录");
        return;
    }
    QJsonObject json;
    json["current_password"] = currentPassword;
    json["new_password"] = newPassword;
    QNetworkReply *reply = m_manager->post(jsonRequest("/api/v1/auth/password"), QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit userOperationFinished(false, apiErrorMessage(reply, "修改密码失败"));
            return;
        }
        clearLocalSessionState();
        emit userOperationFinished(true, "密码已修改，请重新登录");
    });
}

void SearchClient::deactivateAccount()
{
    if (m_token.isEmpty()) {
        emit userOperationFinished(false, "请先登录");
        return;
    }
    QNetworkReply *reply = m_manager->deleteResource(authRequest("/api/v1/auth/me"));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit userOperationFinished(false, apiErrorMessage(reply, "注销账号失败"));
            return;
        }
        clearLocalSessionState();
        emit userOperationFinished(true, "账号已注销");
    });
}

void SearchClient::fetchAdminUsers()
{
    if (m_token.isEmpty() || m_currentRole != "admin") {
        if (!m_adminUserList.isEmpty()) {
            m_adminUserList.clear();
            emit adminUserListChanged();
        }
        return;
    }

    QNetworkReply *reply = m_manager->get(authRequest("/api/v1/admin/users"));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            if (reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt() == 401) {
                refreshAccessToken();
                return;
            }
            emit adminUserOperationFinished(false, apiErrorMessage(reply, "无法获取用户列表"));
            return;
        }
        QJsonArray users = QJsonDocument::fromJson(reply->readAll()).array();
        m_adminUserList.clear();
        for (const QJsonValue &value : users) {
            m_adminUserList.append(value.toObject().toVariantMap());
        }
        emit adminUserListChanged();
    });
}

void SearchClient::createAdminUser(const QString &username, const QString &email, const QString &password, const QString &role)
{
    if (m_token.isEmpty() || m_currentRole != "admin") {
        emit adminUserOperationFinished(false, "需要 admin 权限");
        return;
    }
    const QString trimmedUsername = username.trimmed();
    if (trimmedUsername.isEmpty() || password.length() < 6) {
        emit adminUserOperationFinished(false, "请输入用户名和至少 6 位密码");
        return;
    }

    QJsonObject json;
    json["username"] = trimmedUsername;
    if (!email.trimmed().isEmpty()) {
        json["email"] = email.trimmed().toLower();
    }
    json["password"] = password;
    json["role"] = role.isEmpty() ? "operator" : role;

    QNetworkReply *reply = m_manager->post(jsonRequest("/api/v1/admin/users"), QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit adminUserOperationFinished(false, apiErrorMessage(reply, "创建用户失败"));
            return;
        }
        fetchAdminUsers();
        emit adminUserOperationFinished(true, "用户已创建");
    });
}

void SearchClient::updateAdminUserRole(const QString &username, const QString &role)
{
    if (m_token.isEmpty() || m_currentRole != "admin") {
        emit adminUserOperationFinished(false, "需要 admin 权限");
        return;
    }
    QJsonObject json;
    json["role"] = role;
    QNetworkReply *reply = m_manager->sendCustomRequest(
        jsonRequest("/api/v1/admin/users/" + pathComponent(username)),
        QByteArray("PATCH"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit adminUserOperationFinished(false, apiErrorMessage(reply, "角色更新失败"));
            fetchAdminUsers();
            return;
        }
        fetchAdminUsers();
        fetchUserProfile();
        emit adminUserOperationFinished(true, "角色已更新");
    });
}

void SearchClient::setAdminUserActive(const QString &username, bool isActive)
{
    if (m_token.isEmpty() || m_currentRole != "admin") {
        emit adminUserOperationFinished(false, "需要 admin 权限");
        return;
    }
    QJsonObject json;
    json["is_active"] = isActive;
    QNetworkReply *reply = m_manager->sendCustomRequest(
        jsonRequest("/api/v1/admin/users/" + pathComponent(username)),
        QByteArray("PATCH"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, isActive, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit adminUserOperationFinished(false, apiErrorMessage(reply, isActive ? "启用用户失败" : "禁用用户失败"));
            fetchAdminUsers();
            return;
        }
        fetchAdminUsers();
        emit adminUserOperationFinished(true, isActive ? "用户已启用" : "用户已禁用");
    });
}

void SearchClient::resetAdminUserPassword(const QString &username, const QString &newPassword)
{
    if (m_token.isEmpty() || m_currentRole != "admin") {
        emit adminUserOperationFinished(false, "需要 admin 权限");
        return;
    }
    if (newPassword.length() < 6) {
        emit adminUserOperationFinished(false, "新密码至少 6 位");
        return;
    }
    QJsonObject json;
    json["new_password"] = newPassword;
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/admin/users/" + pathComponent(username) + "/password"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit adminUserOperationFinished(false, apiErrorMessage(reply, "重置密码失败"));
            return;
        }
        fetchAdminUsers();
        emit adminUserOperationFinished(true, "密码已重置，用户需重新登录");
    });
}

void SearchClient::revokeAdminUserTokens(const QString &username)
{
    if (m_token.isEmpty() || m_currentRole != "admin") {
        emit adminUserOperationFinished(false, "需要 admin 权限");
        return;
    }
    QNetworkReply *reply = m_manager->post(
        authRequest("/api/v1/admin/users/" + pathComponent(username) + "/revoke-refresh-tokens"),
        QByteArray()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit adminUserOperationFinished(false, apiErrorMessage(reply, "撤销令牌失败"));
            return;
        }
        fetchAdminUsers();
        emit adminUserOperationFinished(true, "refresh token 已撤销");
    });
}

void SearchClient::ensureDefaultProject()
{
    if (m_token.isEmpty()) return;
    if (m_defaultProjectCreateInFlight) return;
    if (!m_projectList.isEmpty()) {
        m_autoCreateDefaultProject = false;
        return;
    }
    m_autoCreateDefaultProject = true;
    fetchProjects();
}

void SearchClient::fetchProjects()
{
    if (m_token.isEmpty()) {
        m_projectList.clear();
        emit projectListChanged();
        return;
    }

    QNetworkRequest request = authRequest("/api/v1/projects");

    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            if (reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt() == 401) {
                refreshAccessToken();
                return;
            }
            emit errorOccurred("无法获取项目列表");
            return;
        }

        QJsonArray projects = QJsonDocument::fromJson(reply->readAll()).array();
        m_projectList.clear();
        for (const QJsonValue &value : projects) {
            m_projectList.append(value.toObject().toVariantMap());
        }
        emit projectListChanged();

        if (projects.isEmpty()) {
            m_currentProjectId = 0;
            m_currentProjectName.clear();
            m_currentProjectType = isScenicScope() ? "scenic" : "media";
            m_currentSessionId.clear();
            clearCameraStatus();
            persistSession();
            emit currentProjectChanged();
            emit sessionChanged();
            refreshWorkspace();
            if (m_autoCreateDefaultProject && !m_defaultProjectCreateInFlight) {
                m_autoCreateDefaultProject = false;
                createDefaultProject();
            }
            return;
        }
        m_autoCreateDefaultProject = false;

        QJsonObject selectedProject;
        for (const QJsonValue &value : projects) {
            QJsonObject project = value.toObject();
            if (project["id"].toInt() == m_currentProjectId) {
                selectedProject = project;
                break;
            }
        }
        if (selectedProject.isEmpty()) {
            selectedProject = projects.first().toObject();
        }
        setCurrentProject(selectedProject);
        refreshWorkspace();
    });
}

void SearchClient::createDefaultProject()
{
    if (m_token.isEmpty() || m_defaultProjectCreateInFlight) {
        return;
    }
    if (!m_projectList.isEmpty()) {
        m_autoCreateDefaultProject = false;
        return;
    }
    m_defaultProjectCreateInFlight = true;

    QUrl url(m_baseUrl + apiPath("/api/v1/projects"));
    QNetworkRequest request(url);
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    setupAuthHeaders(request);

    QJsonObject json;
    json["name"] = isScenicScope() ? "默认景区项目" : "默认检索项目";
    json["description"] = isScenicScope() ? "景区客户端自动创建" : "Qt 客户端自动创建";

    QNetworkReply *reply = m_manager->post(request, QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        m_defaultProjectCreateInFlight = false;
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred("无法创建默认项目");
            return;
        }

        QJsonObject project = QJsonDocument::fromJson(reply->readAll()).object();
        setCurrentProject(project);
        fetchProjects();
        refreshWorkspace();
    });
}

void SearchClient::createProject(const QString &name, const QString &description)
{
    if (m_token.isEmpty()) {
        emit errorOccurred("请先登录");
        return;
    }

    QJsonObject json;
    json["name"] = name.trimmed();
    json["description"] = description.trimmed();
    if (json["name"].toString().isEmpty()) {
        emit errorOccurred("项目名称不能为空");
        return;
    }

    QNetworkReply *reply = m_manager->post(jsonRequest("/api/v1/projects"), QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
            emit errorOccurred(obj["detail"].toString("创建项目失败"));
            return;
        }
        QJsonObject project = QJsonDocument::fromJson(reply->readAll()).object();
        setCurrentProject(project);
        fetchProjects();
        refreshWorkspace();
    });
}

void SearchClient::createCameraProject(const QString &name, const QString &description, const QString &cameraSource)
{
    if (m_token.isEmpty()) {
        emit errorOccurred("请先登录");
        return;
    }

    const QString trimmedName = name.trimmed();
    const QString source = cameraSource.trimmed().isEmpty() ? "0" : cameraSource.trimmed();
    if (trimmedName.isEmpty()) {
        emit errorOccurred("项目名称不能为空");
        return;
    }

    QJsonObject json;
    json["name"] = trimmedName;
    json["description"] = description.trimmed();
    json["project_type"] = "camera";
    json["camera_source"] = source;

    QNetworkReply *reply = m_manager->post(jsonRequest("/api/v1/projects"), QJsonDocument(json).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, source, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "创建实时相机项目失败"));
            return;
        }
        QJsonObject project = QJsonDocument::fromJson(reply->readAll()).object();
        setCurrentProject(project);
        fetchProjects();
        refreshWorkspace();
        startCamera(source);
    });
}

void SearchClient::deleteProject(int projectId)
{
    if (m_token.isEmpty()) {
        emit errorOccurred("请先登录");
        return;
    }
    if (projectId <= 0) {
        emit errorOccurred("请选择要删除的项目");
        return;
    }

    QNetworkReply *reply = m_manager->deleteResource(authRequest("/api/v1/projects/" + QString::number(projectId)));
    connect(reply, &QNetworkReply::finished, this, [this, projectId, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "删除项目失败"));
            return;
        }
        if (m_currentProjectId == projectId) {
            ++m_projectGeneration;
            if (m_scenicMonitoringReply) m_scenicMonitoringReply->abort();
            m_scenicMonitoringReply.clear();
            m_scenicMonitoringStartInFlight = false;
            m_scenicMonitoringStartedProjectId = 0;
            m_currentProjectId = 0;
            m_currentProjectName.clear();
            m_currentProjectType = isScenicScope() ? "scenic" : "media";
            m_currentSessionId.clear();
            clearCameraStatus();
            persistSession();
            emit currentProjectChanged();
            emit sessionChanged();
        }
        fetchProjects();
        refreshWorkspace();
    });
}

void SearchClient::refreshWorkspace()
{
    if (isScenicScope()) {
        fetchScenicWorkspace();
        return;
    }
    fetchCoreWorkspace();
}

void SearchClient::fetchCoreWorkspace()
{
    fetchPreview();
    fetchMedia();
    fetchJobs();
    fetchAudit();
    fetchHistory();
    fetchCameraStatus();
    if (m_currentRole == "admin") {
        fetchAdminUsers();
    }
    checkInitialStatus();
}

void SearchClient::fetchScenicWorkspace()
{
    fetchPreview();
    fetchMedia();
    fetchJobs();
    fetchAudit();
    fetchHistory();
    fetchCameras();
    fetchScenicDashboard();
    fetchScenicCameraPoints();
    fetchScenicCameraWall();
    fetchTourists();
    checkInitialStatus();
}

void SearchClient::pollStatus()
{
    if (m_currentProjectId <= 0) {
        emit systemStatusChanged(false);
        return;
    }
    if (m_statusReply && m_statusReply->isRunning()) {
        return;
    }
    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    QUrl url(m_baseUrl + apiPath("/api/v1/projects/" + QString::number(projectId) + "/status"));
    QNetworkRequest request(url);
    setupAuthHeaders(request);

    QNetworkReply *reply = m_manager->get(request);
    m_statusReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply, projectId, projectGeneration]() {
        if (m_statusReply == reply) m_statusReply.clear();
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration || projectId != m_currentProjectId) return;
        if (reply->error() == QNetworkReply::NoError) {
            QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
            QJsonObject obj = doc.object();
            QString status = obj["status"].toString();

            if (status == "processing") {
                if (!m_statusTimer->isActive()) {
                    m_statusTimer->start();
                }
                emit systemStatusChanged(false);
                emit progressUpdated(obj["current"].toInt(), obj["total"].toInt());
                fetchJobs();
            } else if (status == "ready") {
                if (m_statusTimer->isActive()) {
                    m_statusTimer->stop();
                    fetchPreview();
                    fetchMedia();
                    fetchJobs();
                    fetchAudit();
                }
                emit systemStatusChanged(true);
            } else {
                emit systemStatusChanged(false);
            }
        }
    });
}
void SearchClient::checkInitialStatus() { pollStatus(); }

void SearchClient::checkEnterpriseReadiness()
{
    if (m_readinessReply && m_readinessReply->isRunning()) {
        return;
    }
    const QString requestBaseUrl = m_baseUrl;
    QUrl url(requestBaseUrl + "/api/v1/readyz");
    QNetworkRequest request(url);
    QNetworkReply *reply = m_manager->get(request);
    m_readinessReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply, requestBaseUrl]() {
        if (m_readinessReply == reply) m_readinessReply.clear();
        reply->deleteLater();
        if (requestBaseUrl != m_baseUrl) return;
        if (reply->error() != QNetworkReply::NoError) {
            m_enterpriseStatus = "系统接口未连接";
            emit enterpriseStatusChanged();
            return;
        }

        QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        const QString status = obj["status"].toString();
        const bool clipLoaded = obj["clip_loaded"].toBool();
        const bool yoloLoaded = obj["yolo_loaded"].toBool();
        const QString device = obj["device"].toString();
        m_enterpriseStatus = QString("系统接口: %1 | CLIP %2 | YOLO %3 | %4")
                                 .arg(status)
                                 .arg(clipLoaded ? "就绪" : "加载中")
                                 .arg(yoloLoaded ? "就绪" : "加载中")
                                 .arg(device);
        emit enterpriseStatusChanged();
    });
}

void SearchClient::clearCameraStatus()
{
    if (m_cameraTimer->isActive()) {
        m_cameraTimer->stop();
    }
    m_cameraRunning = false;
    m_cameraMonitorOnline = false;
    m_cameraFrameUrl.clear();
    m_cameraFrameVersion = 0;
    m_cameraTargetCount = 0;
    m_cameraMessage = "实时相机未启动";
    m_cameraSource = "0";
    m_cameraRecordingUrl.clear();
    m_cameraRecordingName.clear();
    emit cameraStatusChanged();
}

QJsonObject SearchClient::buildSearchFilters(double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId) const
{
    QJsonObject filters;
    if (minSimilarity > 0.0001) {
        filters["min_similarity"] = minSimilarity;
    }
    const QString normalizedCamera = cameraId.trimmed();
    if (!normalizedCamera.isEmpty()) {
        filters["camera_id"] = normalizedCamera;
    }
    const QString normalizedStart = startTime.trimmed();
    if (!normalizedStart.isEmpty()) {
        filters["start_time"] = normalizedStart;
    }
    const QString normalizedEnd = endTime.trimmed();
    if (!normalizedEnd.isEmpty()) {
        filters["end_time"] = normalizedEnd;
    }
    if (mediaId > 0) {
        filters["media_id"] = mediaId;
    }
    return filters;
}

void SearchClient::appendSearchQueryFilters(QUrlQuery &query, double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId, int page, int pageSize) const
{
    query.addQueryItem("page", QString::number(qMax(1, page)));
    query.addQueryItem("page_size", QString::number(qBound(1, pageSize, 100)));
    query.addQueryItem("top_k", "100");
    if (minSimilarity > 0.0001) {
        query.addQueryItem("min_similarity", QString::number(minSimilarity, 'f', 4));
    }
    const QString normalizedCamera = cameraId.trimmed();
    if (!normalizedCamera.isEmpty()) {
        query.addQueryItem("camera_id", normalizedCamera);
    }
    const QString normalizedStart = startTime.trimmed();
    if (!normalizedStart.isEmpty()) {
        query.addQueryItem("start_time", normalizedStart);
    }
    const QString normalizedEnd = endTime.trimmed();
    if (!normalizedEnd.isEmpty()) {
        query.addQueryItem("end_time", normalizedEnd);
    }
    if (mediaId > 0) {
        query.addQueryItem("media_id", QString::number(mediaId));
    }
}

void SearchClient::updateSearchPagination(const QJsonObject &response)
{
    m_lastSearchTaskId = response["task_id"].toInt(m_lastSearchTaskId);
    const QJsonObject pagination = response["pagination"].toObject();
    if (pagination.isEmpty()) {
        const int resultCount = response["results"].toArray().size();
        m_searchPage = 1;
        m_searchPageSize = resultCount;
        m_searchTotal = resultCount;
        m_searchTotalPages = resultCount > 0 ? 1 : 0;
        m_searchHasPrevious = false;
        m_searchHasNext = false;
    } else {
        m_searchPage = pagination["page"].toInt(1);
        m_searchPageSize = pagination["page_size"].toInt(12);
        m_searchTotal = pagination["total"].toInt(0);
        m_searchTotalPages = pagination["total_pages"].toInt(0);
        m_searchHasPrevious = pagination["has_previous"].toBool(false);
        m_searchHasNext = pagination["has_next"].toBool(false);
    }
    emit searchPaginationChanged();
}

void SearchClient::updateScenicCameraWall(const QJsonArray &wall)
{
    m_scenicCameraWall.clear();
    for (const QJsonValue &value : wall) {
        m_scenicCameraWall.append(value.toObject().toVariantMap());
    }
    emit scenicCameraWallChanged();

    QJsonObject firstStatus;
    for (const QJsonValue &value : wall) {
        const QJsonObject point = value.toObject();
        const QJsonObject status = point["status"].toObject();
        if (!status.isEmpty()) {
            if (firstStatus.isEmpty()) {
                firstStatus = status;
            }
            if (status["running"].toBool()) {
                updateCameraStatus(status);
                return;
            }
        }
    }
    if (!firstStatus.isEmpty()) {
        updateCameraStatus(firstStatus);
    } else if (wall.isEmpty()) {
        clearCameraStatus();
    }
}

void SearchClient::updateCameraStatus(const QJsonObject &status)
{
    m_cameraRunning = status["running"].toBool();
    const bool monitorOnline = status.contains("monitor_running")
        ? status["monitor_running"].toBool()
        : !status["latest_frame_url"].toString().isEmpty();
    m_cameraMonitorOnline = monitorOnline;
    const QString statusSource = status["camera_source"].toString();
    m_cameraSource = statusSource.isEmpty() ? (m_cameraSource.isEmpty() ? "0" : m_cameraSource) : statusSource;
    const QString frameUrl = status["latest_frame_url"].toString();
    const int frameVersion = status["latest_frame_version"].toInt();
    if (frameUrl.isEmpty()) {
        m_cameraFrameUrl.clear();
        m_cameraFrameVersion = 0;
    } else if (frameUrl != m_cameraFrameUrl || frameVersion != m_cameraFrameVersion) {
        m_cameraFrameUrl = frameUrl;
        m_cameraFrameVersion = frameVersion > 0 ? frameVersion : (m_cameraFrameVersion + 1);
    }
    m_cameraTargetCount = status["indexed_targets"].toInt();
    const QString error = status["error"].toString();
    m_cameraMessage = error.isEmpty() ? status["message"].toString("实时相机状态已更新") : error;
    const QString recordingUrl = status["recording_url"].toString();
    if (!recordingUrl.isEmpty() || !m_cameraRunning) {
        m_cameraRecordingUrl = recordingUrl;
        m_cameraRecordingName = status["recording_filename"].toString();
    }
    if (monitorOnline && !m_cameraTimer->isActive()) {
        m_cameraTimer->start();
    } else if (!monitorOnline && m_cameraTimer->isActive()) {
        m_cameraTimer->stop();
    }
    persistSession();
    emit cameraStatusChanged();
}

void SearchClient::startCamera(const QString &cameraSource)
{
    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先选择实时相机项目");
        return;
    }
    if (m_currentProjectType != "camera") {
        emit errorOccurred("当前项目不是实时相机项目");
        return;
    }

    const QString source = cameraSource.trimmed().isEmpty() ? m_cameraSource : cameraSource.trimmed();
    QJsonObject json;
    json["camera_source"] = source.trimmed().isEmpty() ? "0" : source.trimmed();
    json["sample_interval_seconds"] = 1.0;
    json["min_confidence"] = 0.35;
    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;

    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/projects/" + QString::number(projectId) + "/camera/start"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply, projectId, projectGeneration]() {
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration || projectId != m_currentProjectId) return;
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "启用实时入库失败"));
            fetchCameraStatus();
            return;
        }
        updateCameraStatus(QJsonDocument::fromJson(reply->readAll()).object());
        m_statusTimer->start();
        refreshWorkspace();
    });
}

void SearchClient::stopCamera()
{
    if (m_currentProjectId <= 0 || m_currentProjectType != "camera") {
        return;
    }

    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    QNetworkReply *reply = m_manager->post(
        authRequest("/api/v1/projects/" + QString::number(projectId) + "/camera/stop"),
        QByteArray()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply, projectId, projectGeneration]() {
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration || projectId != m_currentProjectId) return;
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "暂停实时入库失败"));
            return;
        }
        updateCameraStatus(QJsonDocument::fromJson(reply->readAll()).object());
        refreshWorkspace();
    });
}

void SearchClient::fetchCameraStatus()
{
    if (m_currentProjectId <= 0 || m_token.isEmpty()) {
        clearCameraStatus();
        return;
    }
    if (m_currentProjectType != "camera") {
        if (isScenicScope()) {
            fetchScenicCameraWall();
        } else {
            clearCameraStatus();
        }
        return;
    }
    if (m_cameraReply && m_cameraReply->isRunning()) {
        return;
    }

    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    QNetworkReply *reply = m_manager->get(
        authRequest("/api/v1/projects/" + QString::number(projectId) + "/camera/status")
    );
    m_cameraReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply, projectId, projectGeneration]() {
        if (m_cameraReply == reply) m_cameraReply.clear();
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration || projectId != m_currentProjectId) return;
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        updateCameraStatus(QJsonDocument::fromJson(reply->readAll()).object());
    });
}

void SearchClient::searchByText(const QString &text) {
    searchByTextAdvanced(text, 0.0, "", "", "", 0, 1, m_searchPageSize);
}

void SearchClient::searchByTextAdvanced(const QString &text, double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId, int page, int pageSize) {
    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先登录并创建项目");
        return;
    }

    QUrl url(m_baseUrl + "/api/v1/search/text");

    QNetworkRequest request(url);
    request.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    setupAuthHeaders(request);

    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    const quint64 searchGeneration = ++m_searchGeneration;
    if (m_searchReply) m_searchReply->abort();

    QJsonObject json;
    json["project_id"] = projectId;
    json["query_text"] = text;
    json["top_k"] = 100;
    json["page"] = qMax(1, page);
    json["page_size"] = qBound(1, pageSize, 100);
    json["filters"] = buildSearchFilters(minSimilarity, cameraId, startTime, endTime, mediaId);

    QNetworkReply *reply = m_manager->post(request, QJsonDocument(json).toJson());
    m_searchReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply, projectId, projectGeneration, searchGeneration]() {
        if (m_searchReply == reply) m_searchReply.clear();
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration
            || projectId != m_currentProjectId
            || searchGeneration != m_searchGeneration) return;
        handleNetworkReply(reply);
    });
}

void SearchClient::uploadVideo(const QString &fileUrl, bool isAppend)
{
    Q_UNUSED(isAppend)
    QString localFilePath = QUrl(fileUrl).toLocalFile();
    QFile *file = new QFile(localFilePath);
    if (!file->open(QIODevice::ReadOnly)) {
        emit errorOccurred("无法打开视频文件");
        delete file; return;
    }

    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart videoPart;
    videoPart.setHeader(QNetworkRequest::ContentTypeHeader, QVariant("video/mp4")); // 视频 MIME
    videoPart.setHeader(QNetworkRequest::ContentDispositionHeader,
                        QVariant(QString("form-data; name=\"file\"; filename=\"%1\"").arg(QFileInfo(*file).fileName())));
    videoPart.setBodyDevice(file);
    file->setParent(multiPart);
    multiPart->append(videoPart);

    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先登录并创建项目");
        delete multiPart;
        return;
    }

    QUrl url(m_baseUrl + apiPath("/api/v1/projects/" + QString::number(m_currentProjectId) + "/media"));

    QNetworkRequest request(url);
    setupAuthHeaders(request);

    QNetworkReply *reply = m_manager->post(request, multiPart);
    multiPart->setParent(reply);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "视频上传失败"));
            refreshWorkspace();
            return;
        }
        m_statusTimer->start();
        refreshWorkspace();
    });
}
void SearchClient::uploadGallery(const QVariantList &fileUrls, bool isAppend)
{
    Q_UNUSED(isAppend)
    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先登录并创建项目");
        return;
    }

    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    int appendedFiles = 0;
    for (const QVariant &urlVar : fileUrls) {
        QString localFilePath = QUrl(urlVar.toString()).toLocalFile();
        QFile *file = new QFile(localFilePath);
        if (file->open(QIODevice::ReadOnly)) {
            QHttpPart imagePart;
            imagePart.setHeader(QNetworkRequest::ContentTypeHeader, QVariant("image/jpeg"));
            imagePart.setHeader(QNetworkRequest::ContentDispositionHeader,
                                QVariant(QString("form-data; name=\"files\"; filename=\"%1\"").arg(QFileInfo(*file).fileName())));
            imagePart.setBodyDevice(file);
            file->setParent(multiPart);
            multiPart->append(imagePart);
            appendedFiles += 1;
        } else {
            delete file;
        }
    }
    if (appendedFiles == 0) {
        delete multiPart;
        emit errorOccurred("未选择可读取的图片文件");
        return;
    }

    QUrl url(m_baseUrl + apiPath("/api/v1/projects/" + QString::number(m_currentProjectId) + "/media/batch"));

    QNetworkRequest request(url);
    setupAuthHeaders(request);

    QNetworkReply *reply = m_manager->post(request, multiPart);
    multiPart->setParent(reply);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "图片上传失败"));
            refreshWorkspace();
            return;
        }
        m_statusTimer->start();
        refreshWorkspace();
    });
}

void SearchClient::fetchMedia()
{
    if (m_currentProjectId <= 0) {
        m_mediaList.clear();
        emit mediaListChanged();
        return;
    }
    QNetworkRequest request = authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/media");
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        QJsonArray arr = QJsonDocument::fromJson(reply->readAll()).object()["media"].toArray();
        m_mediaList.clear();
        for (const QJsonValue &val : arr) {
            m_mediaList.append(val.toObject().toVariantMap());
        }
        emit mediaListChanged();
    });
}

void SearchClient::deleteMedia(int mediaId)
{
    QNetworkRequest request = authRequest("/api/v1/media/" + QString::number(mediaId));
    QNetworkReply *reply = m_manager->deleteResource(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
            emit errorOccurred(obj["detail"].toString("删除媒体失败"));
            return;
        }
        refreshWorkspace();
    });
}

void SearchClient::clearProjectMedia()
{
    if (m_currentProjectId <= 0) {
        emit errorOccurred("请选择项目");
        return;
    }
    QNetworkRequest request = authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/media");
    QNetworkReply *reply = m_manager->deleteResource(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "清空媒体失败"));
            return;
        }
        refreshWorkspace();
    });
}

void SearchClient::fetchJobs()
{
    if (m_currentProjectId <= 0) {
        m_jobList.clear();
        emit jobListChanged();
        return;
    }
    QNetworkRequest request = authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/jobs");
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        QJsonArray arr = QJsonDocument::fromJson(reply->readAll()).object()["jobs"].toArray();
        m_jobList.clear();
        for (const QJsonValue &val : arr) {
            m_jobList.append(val.toObject().toVariantMap());
        }
        emit jobListChanged();
    });
}

void SearchClient::fetchAudit()
{
    if (m_currentProjectId <= 0) {
        m_auditList.clear();
        emit auditListChanged();
        return;
    }
    QNetworkRequest request = authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/audit");
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        QJsonArray arr = QJsonDocument::fromJson(reply->readAll()).object()["audit"].toArray();
        m_auditList.clear();
        for (const QJsonValue &val : arr) {
            m_auditList.append(val.toObject().toVariantMap());
        }
        emit auditListChanged();
    });
}

void SearchClient::fetchCameras()
{
    if (m_token.isEmpty()) {
        m_availableCameraList.clear();
        emit availableCameraListChanged();
        return;
    }

    QNetworkReply *reply = m_manager->get(authRequest("/api/v1/cameras"));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        QJsonArray devices = QJsonDocument::fromJson(reply->readAll()).object()["devices"].toArray();
        m_availableCameraList.clear();
        for (const QJsonValue &value : devices) {
            QJsonObject obj = value.toObject();
            QString label = obj["label"].toString();
            QString source = obj["source"].toString();
            if (label.isEmpty()) {
                label = "摄像头 " + source;
            }
            if (obj["type"].toString() == "iphone" && !label.contains("iPhone", Qt::CaseInsensitive)) {
                label += " (iPhone)";
            }
            obj["label"] = label + "  [" + source + "]";
            m_availableCameraList.append(obj.toVariantMap());
        }
        emit availableCameraListChanged();
    });
}

void SearchClient::fetchScenicDashboard()
{
    if (m_currentProjectId <= 0 || m_token.isEmpty()) {
        m_scenicDashboard.clear();
        emit scenicDashboardChanged();
        return;
    }
    QNetworkReply *reply = m_manager->get(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/dashboard")
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        m_scenicDashboard = QJsonDocument::fromJson(reply->readAll()).object().toVariantMap();
        emit scenicDashboardChanged();
    });
}

void SearchClient::takeScenicCameraSnapshot()
{
    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先选择项目");
        return;
    }
    
    QNetworkRequest req = authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera/snapshot");
    req.setHeader(QNetworkRequest::ContentTypeHeader, "application/json");
    
    QNetworkReply *reply = m_manager->post(req, QByteArray());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "抓拍失败"));
            return;
        }
        
        QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        QString url = obj["snapshot_url"].toString();
        QString filename = obj["snapshot_filename"].toString();
        
        // Ensure absolute URL
        if (url.startsWith("/")) {
            url = m_baseUrl + url;
        }
        
        // We can just emit the URL, and if the user wants to download it to local we can do it,
        // or just let QML download it. However, registerTourist takes a local file.
        // We need to download this image to a temporary file.
        
        QNetworkRequest dlReq((QUrl(url)));
        QNetworkReply *dlReply = m_manager->get(dlReq);
        connect(dlReply, &QNetworkReply::finished, this, [this, dlReply, url, filename]() {
            dlReply->deleteLater();
            if (dlReply->error() == QNetworkReply::NoError) {
                QString tempPath = QDir::temp().filePath(filename.isEmpty() ? "snapshot.jpg" : filename);
                QFile file(tempPath);
                if (file.open(QIODevice::WriteOnly)) {
                    file.write(dlReply->readAll());
                    file.close();
                    emit scenicCameraSnapshotReady("file://" + tempPath, filename);
                    emit scenicOperationFinished(true, "相机抓拍成功");
                } else {
                    emit errorOccurred("无法保存抓拍图片到本地临时文件");
                }
            } else {
                emit errorOccurred("抓拍成功，但下载图片失败");
            }
        });
    });
}

void SearchClient::uploadScenicSimulationVideo(int pointId, const QString &fileUrl)
{
    QString localFilePath = QUrl(fileUrl).toLocalFile();
    QFile *file = new QFile(localFilePath);
    if (!file->open(QIODevice::ReadOnly)) {
        emit errorOccurred("无法打开模拟视频文件");
        delete file; return;
    }

    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart videoPart;
    videoPart.setHeader(QNetworkRequest::ContentTypeHeader, QVariant("video/mp4"));
    videoPart.setHeader(QNetworkRequest::ContentDispositionHeader,
                        QVariant(QString("form-data; name=\"file\"; filename=\"%1\"").arg(QFileInfo(*file).fileName())));
    videoPart.setBodyDevice(file);
    file->setParent(multiPart);
    multiPart->append(videoPart);

    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先选择项目");
        delete multiPart;
        return;
    }

    QUrl url(m_baseUrl + apiPath("/api/v1/scenic/projects/" + QString::number(m_currentProjectId) + "/camera-points/" + QString::number(pointId) + "/simulate-video"));

    QNetworkRequest request(url);
    setupAuthHeaders(request);

    QNetworkReply *reply = m_manager->post(request, multiPart);
    multiPart->setParent(reply);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "模拟视频上传失败"));
            return;
        }
        emit scenicOperationFinished(true, "模拟视频已上传，正在后台处理特征");
        m_statusTimer->start();
        fetchScenicCameraWall();
        fetchMedia();
        fetchJobs();
        fetchAudit();
        fetchScenicDashboard();
    });
}

void SearchClient::fetchScenicCameraPoints()
{
    if (m_currentProjectId <= 0 || m_token.isEmpty()) {
        m_scenicCameraPointList.clear();
        emit scenicCameraPointListChanged();
        return;
    }
    QNetworkReply *reply = m_manager->get(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera-points")
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        QJsonArray arr = QJsonDocument::fromJson(reply->readAll()).object()["camera_points"].toArray();
        m_scenicCameraPointList.clear();
        for (const QJsonValue &value : arr) {
            m_scenicCameraPointList.append(value.toObject().toVariantMap());
        }
        emit scenicCameraPointListChanged();
    });
}

void SearchClient::fetchScenicCameraWall()
{
    if (m_currentProjectId <= 0 || m_token.isEmpty()) {
        m_scenicCameraWall.clear();
        emit scenicCameraWallChanged();
        return;
    }
    QNetworkReply *reply = m_manager->get(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera-wall")
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        const QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        updateScenicCameraWall(obj["camera_wall"].toArray());
        fetchPreview();
        fetchMedia();
    });
}

void SearchClient::startScenicMonitoring()
{
    if (m_currentProjectId <= 0) {
        emit scenicOperationFinished(false, "请先选择项目");
        return;
    }
    if (m_scenicMonitoringStartInFlight) {
        return;
    }
    m_scenicMonitoringStartInFlight = true;
    QJsonObject json;
    json["camera_source"] = m_cameraSource.trimmed().isEmpty() ? "0" : m_cameraSource.trimmed();
    json["sample_interval_seconds"] = 1.0;
    json["min_confidence"] = 0.35;
    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/projects/" + QString::number(projectId) + "/scenic/camera-points/start-all"),
        QJsonDocument(json).toJson()
    );
    m_scenicMonitoringReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, projectId, projectGeneration, reply]() {
        reply->deleteLater();
        if (m_scenicMonitoringReply == reply) {
            m_scenicMonitoringReply.clear();
            m_scenicMonitoringStartInFlight = false;
        }
        if (projectGeneration != m_projectGeneration || projectId != m_currentProjectId) return;
        if (reply->error() != QNetworkReply::NoError) {
            m_scenicMonitoringStartedProjectId = 0;
            emit scenicOperationFinished(false, apiErrorMessage(reply, "启动景区监控墙失败"));
            fetchScenicCameraWall();
            return;
        }
        const QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        updateScenicCameraWall(obj["camera_wall"].toArray());
        if (m_scenicCameraWall.isEmpty() && obj.contains("camera_status")) {
            updateCameraStatus(obj["camera_status"].toObject());
        }
        m_scenicMonitoringStartedProjectId = projectId;
        fetchScenicDashboard();
        fetchScenicCameraPoints();
        fetchAudit();
        emit scenicOperationFinished(true, "景区监控墙已启动");
    });
}

void SearchClient::stopScenicMonitoring()
{
    if (m_currentProjectId <= 0) {
        emit scenicOperationFinished(false, "请先选择项目");
        return;
    }
    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    QNetworkReply *reply = m_manager->post(
        authRequest("/api/v1/projects/" + QString::number(projectId) + "/scenic/camera-points/stop-all"),
        QByteArray()
    );
    connect(reply, &QNetworkReply::finished, this, [this, projectId, projectGeneration, reply]() {
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration || projectId != m_currentProjectId) return;
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "停止景区监控墙失败"));
            return;
        }
        const QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        updateScenicCameraWall(obj["camera_wall"].toArray());
        m_scenicMonitoringStartedProjectId = 0;
        fetchScenicDashboard();
        fetchAudit();
        emit scenicOperationFinished(true, "景区监控墙已停止");
    });
}

void SearchClient::syncScenicCameraPoints()
{
    if (m_currentProjectId <= 0) {
        emit scenicOperationFinished(false, "请先选择项目");
        return;
    }
    QNetworkReply *reply = m_manager->post(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera-points/discover"),
        QByteArray()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "同步本机相机失败"));
            return;
        }
        const QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        QJsonArray arr = obj["camera_points"].toArray();
        m_scenicCameraPointList.clear();
        for (const QJsonValue &value : arr) {
            m_scenicCameraPointList.append(value.toObject().toVariantMap());
        }
        emit scenicCameraPointListChanged();
        fetchScenicCameraWall();
        fetchScenicDashboard();
        fetchAudit();
        emit scenicOperationFinished(true, QString("已同步 %1 个新点位").arg(obj["created"].toInt()));
    });
}

void SearchClient::createScenicCameraPoint(const QString &name, const QString &cameraSource, const QString &role)
{
    if (m_currentProjectId <= 0) {
        emit scenicOperationFinished(false, "请先选择项目");
        return;
    }
    const QString trimmedName = name.trimmed();
    if (trimmedName.isEmpty()) {
        emit scenicOperationFinished(false, "请输入点位名称");
        return;
    }
    const QString source = cameraSource.trimmed().isEmpty() ? "0" : cameraSource.trimmed();
    QJsonObject json;
    json["name"] = trimmedName;
    json["camera_source"] = source;
    json["camera_id"] = source;
    json["role"] = role.trimmed().isEmpty() ? "play" : role.trimmed();
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera-points"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "新增点位失败"));
            return;
        }
        fetchScenicCameraPoints();
        fetchScenicDashboard();
        fetchAudit();
        emit scenicOperationFinished(true, "点位已新增");
    });
}

void SearchClient::startScenicCamera(const QString &cameraSource)
{
    if (m_currentProjectId <= 0) {
        emit scenicOperationFinished(false, "请先选择项目");
        return;
    }
    const QString source = cameraSource.trimmed().isEmpty() ? m_cameraSource : cameraSource.trimmed();
    QJsonObject json;
    json["camera_source"] = source.trimmed().isEmpty() ? "0" : source.trimmed();
    json["sample_interval_seconds"] = 1.0;
    json["min_confidence"] = 0.35;
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera/start"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "启动景区相机失败"));
            fetchScenicCameraStatus();
            return;
        }
        updateCameraStatus(QJsonDocument::fromJson(reply->readAll()).object());
        fetchScenicCameraWall();
        fetchScenicDashboard();
        fetchAudit();
        emit scenicOperationFinished(true, "景区相机已启动");
    });
}

void SearchClient::stopScenicCamera()
{
    fetchScenicCameraStatus();
    fetchScenicCameraWall();
    emit scenicOperationFinished(true, "统一监控持续运行，景区端不再关闭相机");
}

void SearchClient::fetchScenicCameraStatus()
{
    if (m_currentProjectId <= 0 || m_token.isEmpty()) {
        return;
    }
    QNetworkReply *reply = m_manager->get(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/scenic/camera/status")
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        updateCameraStatus(QJsonDocument::fromJson(reply->readAll()).object());
        fetchPreview();
        fetchMedia();
    });
}

void SearchClient::registerTourist(const QString &fileUrl, const QString &ticketCode, const QString &visitorLabel, const QString &contactHint)
{
    if (m_currentProjectId <= 0) {
        emit scenicOperationFinished(false, "请先选择项目");
        return;
    }
    QString localFilePath = QUrl(fileUrl).toLocalFile();
    if (localFilePath.isEmpty()) {
        localFilePath = fileUrl;
    }
    QFile *file = new QFile(localFilePath);
    if (!file->open(QIODevice::ReadOnly)) {
        emit scenicOperationFinished(false, "无法打开入园照片");
        delete file;
        return;
    }

    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    auto appendTextPart = [multiPart](const QString &name, const QString &value) {
        QHttpPart part;
        part.setHeader(QNetworkRequest::ContentDispositionHeader, QVariant(QString("form-data; name=\"%1\"").arg(name)));
        part.setBody(value.toUtf8());
        multiPart->append(part);
    };
    appendTextPart("ticket_code", ticketCode.trimmed());
    appendTextPart("visitor_label", visitorLabel.trimmed());
    appendTextPart("contact_hint", contactHint.trimmed());
    appendTextPart("consent_granted", "true");

    QHttpPart imagePart;
    imagePart.setHeader(QNetworkRequest::ContentTypeHeader, QVariant("image/jpeg"));
    imagePart.setHeader(
        QNetworkRequest::ContentDispositionHeader,
        QVariant(QString("form-data; name=\"file\"; filename=\"%1\"").arg(QFileInfo(*file).fileName()))
    );
    imagePart.setBodyDevice(file);
    file->setParent(multiPart);
    multiPart->append(imagePart);

    QNetworkReply *reply = m_manager->post(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/tourists"),
        multiPart
    );
    multiPart->setParent(reply);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "入园登记失败"));
            return;
        }
        m_selectedTouristSession = QJsonDocument::fromJson(reply->readAll()).object().toVariantMap();
        emit selectedTouristSessionChanged();
        fetchTourists();
        fetchScenicDashboard();
        fetchAudit();
        emit scenicOperationFinished(true, "入园游客已登记");
    });
}

void SearchClient::fetchTourists()
{
    if (m_currentProjectId <= 0 || m_token.isEmpty()) {
        m_touristSessionList.clear();
        emit touristSessionListChanged();
        return;
    }
    QNetworkReply *reply = m_manager->get(
        authRequest("/api/v1/projects/" + QString::number(m_currentProjectId) + "/tourists")
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;
        }
        QJsonArray arr = QJsonDocument::fromJson(reply->readAll()).object()["tourists"].toArray();
        m_touristSessionList.clear();
        for (const QJsonValue &value : arr) {
            m_touristSessionList.append(value.toObject().toVariantMap());
        }
        emit touristSessionListChanged();
    });
}

void SearchClient::selectTouristSession(int sessionId)
{
    if (sessionId <= 0) {
        m_selectedTouristSession.clear();
        emit selectedTouristSessionChanged();
        return;
    }
    const quint64 selectionGeneration = ++m_touristSelectionGeneration;
    const quint64 projectGeneration = m_projectGeneration;
    QNetworkReply *reply = m_manager->get(authRequest("/api/v1/tourists/" + QString::number(sessionId)));
    connect(reply, &QNetworkReply::finished, this, [this, reply, selectionGeneration, projectGeneration]() {
        reply->deleteLater();
        if (selectionGeneration != m_touristSelectionGeneration || projectGeneration != m_projectGeneration) return;
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "读取游客记录失败"));
            return;
        }
        m_selectedTouristSession = QJsonDocument::fromJson(reply->readAll()).object().toVariantMap();
        emit selectedTouristSessionChanged();
    });
}

void SearchClient::matchTourist(int sessionId)
{
    if (sessionId <= 0) {
        emit scenicOperationFinished(false, "请先选择游客记录");
        return;
    }
    QJsonObject json;
    json["top_k"] = 100;
    json["min_similarity"] = 0.2;
    json["clip_padding_seconds"] = 2.0;
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/tourists/" + QString::number(sessionId) + "/match"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, sessionId, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "出园匹配失败"));
            return;
        }
        const QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        const int segmentCount = obj["segments"].toArray().size();
        const QJsonObject tourist = obj["tourist"].toObject();
        if (!tourist.isEmpty()) {
            m_selectedTouristSession = tourist.toVariantMap();
            emit selectedTouristSessionChanged();
        }
        fetchTourists();
        fetchScenicDashboard();
        fetchHistory();
        fetchAudit();
        selectTouristSession(sessionId);
        emit scenicOperationFinished(true, segmentCount > 0 ? QString("出园匹配完成，已生成 %1 个候选片段").arg(segmentCount) : "出园匹配完成，暂无可用片段");
    });
}

void SearchClient::updateTouristSegment(int sessionId, int segmentId, bool included, int sortOrder)
{
    if (sessionId <= 0 || segmentId <= 0) {
        emit scenicOperationFinished(false, "片段编号无效");
        return;
    }
    QJsonObject json;
    json["included"] = included;
    if (sortOrder >= 0) {
        json["sort_order"] = sortOrder;
    }
    QNetworkReply *reply = m_manager->sendCustomRequest(
        jsonRequest("/api/v1/tourists/" + QString::number(sessionId) + "/segments/" + QString::number(segmentId)),
        "PATCH",
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "更新片段失败"));
            return;
        }
        fetchAudit();
        emit scenicOperationFinished(true, "片段已更新");
    });
}

void SearchClient::updateTouristSegments(int sessionId, const QVariantList &segmentIds, bool included)
{
    if (sessionId <= 0 || segmentIds.isEmpty()) {
        emit scenicOperationFinished(false, "请选择要更新的片段");
        return;
    }
    QJsonArray ids;
    for (const QVariant &value : segmentIds) {
        const int segmentId = value.toInt();
        if (segmentId > 0) {
            ids.append(segmentId);
        }
    }
    if (ids.isEmpty()) {
        emit scenicOperationFinished(false, "请选择要更新的片段");
        return;
    }

    QJsonObject json;
    json["segment_ids"] = ids;
    json["included"] = included;
    QNetworkReply *reply = m_manager->sendCustomRequest(
        jsonRequest("/api/v1/tourists/" + QString::number(sessionId) + "/segments"),
        "PATCH",
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, ids, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "批量更新片段失败"));
            return;
        }
        fetchAudit();
        emit scenicOperationFinished(true, QString("已更新 %1 个片段").arg(ids.size()));
    });
}

void SearchClient::generateTouristVlog(int sessionId)
{
    if (sessionId <= 0) {
        emit scenicOperationFinished(false, "请先选择游客记录");
        return;
    }
    QNetworkReply *reply = m_manager->post(authRequest("/api/v1/tourists/" + QString::number(sessionId) + "/vlog"), QByteArray());
    connect(reply, &QNetworkReply::finished, this, [this, sessionId, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "提交成片失败"));
            return;
        }
        m_statusTimer->start();
        fetchJobs();
        fetchScenicDashboard();
        fetchAudit();
        selectTouristSession(sessionId);
        emit scenicOperationFinished(true, "成片任务已提交");
    });
}

void SearchClient::createScenicOrder(int sessionId, int amountCents)
{
    if (sessionId <= 0) {
        emit scenicOperationFinished(false, "请先选择游客记录");
        return;
    }
    QJsonObject json;
    json["amount_cents"] = qMax(0, amountCents);
    json["currency"] = "CNY";
    json["payment_method"] = "manual";
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/scenic/tourists/" + QString::number(sessionId) + "/orders"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, sessionId, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "创建订单失败"));
            return;
        }
        fetchScenicDashboard();
        fetchAudit();
        selectTouristSession(sessionId);
        emit scenicOperationFinished(true, "订单已创建");
    });
}

void SearchClient::createWechatScenicPayment(int sessionId, int amountCents)
{
    if (sessionId <= 0) {
        emit scenicOperationFinished(false, "请先选择游客记录");
        return;
    }
    QJsonObject json;
    json["amount_cents"] = qMax(0, amountCents);
    json["currency"] = "CNY";
    json["payment_method"] = "wechat_native";
    QNetworkReply *reply = m_manager->post(
        jsonRequest("/api/v1/scenic/tourists/" + QString::number(sessionId) + "/orders"),
        QJsonDocument(json).toJson()
    );
    connect(reply, &QNetworkReply::finished, this, [this, sessionId, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "创建微信支付订单失败"));
            return;
        }
        const QJsonObject order = QJsonDocument::fromJson(reply->readAll()).object();
        fetchScenicDashboard();
        fetchAudit();
        selectTouristSession(sessionId);
        emit scenicOperationFinished(true, "微信支付订单已创建 " + order["payment_code"].toString());
    });
}

void SearchClient::refreshScenicOrderPayment(int orderId)
{
    if (orderId <= 0) {
        emit scenicOperationFinished(false, "订单编号无效");
        return;
    }
    QNetworkReply *reply = m_manager->post(authRequest("/api/v1/scenic/orders/" + QString::number(orderId) + "/refresh-payment"), QByteArray());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "刷新支付状态失败"));
            return;
        }
        fetchScenicDashboard();
        fetchAudit();
        if (m_selectedTouristSession.value("id").toInt() > 0) {
            selectTouristSession(m_selectedTouristSession.value("id").toInt());
        }
        emit scenicOperationFinished(true, "支付状态已刷新");
    });
}

void SearchClient::markScenicOrderPaid(int orderId)
{
    if (orderId <= 0) {
        emit scenicOperationFinished(false, "订单编号无效");
        return;
    }
    QNetworkReply *reply = m_manager->post(authRequest("/api/v1/scenic/orders/" + QString::number(orderId) + "/mark-paid"), QByteArray());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit scenicOperationFinished(false, apiErrorMessage(reply, "确认支付失败"));
            return;
        }
        fetchScenicDashboard();
        fetchAudit();
        if (m_selectedTouristSession.value("id").toInt() > 0) {
            selectTouristSession(m_selectedTouristSession.value("id").toInt());
        }
        emit scenicOperationFinished(true, "订单已确认支付");
    });
}

void SearchClient::fetchHistory()
{
    if (m_token.isEmpty()) {
        m_historyList.clear();
        emit historyListChanged();
        return;
    }
    QUrl url(m_baseUrl + apiPath("/api/v1/history"));
    QNetworkRequest request(url);
    setupAuthHeaders(request);

    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
            QJsonArray arr = doc.object()["history"].toArray();
            m_historyList.clear();
            for (const QJsonValue &val : arr) {
                m_historyList.append(val.toObject().toVariantMap());
            }
            emit historyListChanged();
        }
    });
}

void SearchClient::cleanupSession()
{
    m_statusTimer->stop();
    m_cameraTimer->stop();
}
void SearchClient::searchByImage(const QString &fileUrl) {
    searchByImageAdvanced(fileUrl, 0.0, "", "", "", 0, 1, m_searchPageSize);
}

void SearchClient::searchByImageAdvanced(const QString &fileUrl, double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId, int page, int pageSize) {
    QString localFilePath = QUrl(fileUrl).toLocalFile();
    QFile *file = new QFile(localFilePath);
    if (!file->open(QIODevice::ReadOnly)) {
        emit errorOccurred("无法打开图片文件");
        delete file; return;
    }

    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart imagePart;
    imagePart.setHeader(QNetworkRequest::ContentTypeHeader, QVariant("image/jpeg"));
    imagePart.setHeader(QNetworkRequest::ContentDispositionHeader,
                        QVariant(QString("form-data; name=\"file\"; filename=\"%1\"").arg(QFileInfo(*file).fileName())));
    imagePart.setBodyDevice(file);
    file->setParent(multiPart);
    multiPart->append(imagePart);

    if (m_currentProjectId <= 0) {
        emit errorOccurred("请先登录并创建项目");
        delete multiPart;
        return;
    }

    const int projectId = m_currentProjectId;
    const quint64 projectGeneration = m_projectGeneration;
    const quint64 searchGeneration = ++m_searchGeneration;
    if (m_searchReply) m_searchReply->abort();

    QUrl url(m_baseUrl + "/api/v1/search/image");
    QUrlQuery query;
    query.addQueryItem("project_id", QString::number(projectId));
    appendSearchQueryFilters(query, minSimilarity, cameraId, startTime, endTime, mediaId, page, pageSize);
    url.setQuery(query);
    QNetworkRequest request(url);
    setupAuthHeaders(request);

    QNetworkReply *reply = m_manager->post(request, multiPart);
    multiPart->setParent(reply);
    m_searchReply = reply;
    connect(reply, &QNetworkReply::finished, this, [this, reply, projectId, projectGeneration, searchGeneration]() {
        if (m_searchReply == reply) m_searchReply.clear();
        reply->deleteLater();
        if (projectGeneration != m_projectGeneration
            || projectId != m_currentProjectId
            || searchGeneration != m_searchGeneration) return;
        handleNetworkReply(reply);
    });
}

void SearchClient::fetchSearchTaskPage(int taskId, int page, int pageSize)
{
    if (taskId <= 0) {
        emit errorOccurred("请先完成一次检索");
        return;
    }
    m_lastSearchTaskId = taskId;
    QUrl url(m_baseUrl + apiPath("/api/v1/history/" + QString::number(taskId)));
    QUrlQuery query;
    query.addQueryItem("page", QString::number(qMax(1, page)));
    query.addQueryItem("page_size", QString::number(qBound(1, pageSize, 100)));
    url.setQuery(query);
    QNetworkRequest request(url);
    setupAuthHeaders(request);
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "读取检索分页失败"));
            return;
        }
        QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        updateSearchPagination(obj);
        QJsonArray resultsArray = obj["results"].toArray();
        QVariantList resultList;
        for (const QJsonValue &value : resultsArray) {
            resultList.append(value.toObject().toVariantMap());
        }
        emit searchFinished(resultList);
    });
}

void SearchClient::fetchSearchResultDetail(int resultId)
{
    if (resultId <= 0) {
        emit errorOccurred("检索结果缺少详情编号");
        return;
    }
    QNetworkReply *reply = m_manager->get(authRequest("/api/v1/search/results/" + QString::number(resultId)));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit errorOccurred(apiErrorMessage(reply, "无法获取结果详情"));
            return;
        }
        m_searchResultDetail = QJsonDocument::fromJson(reply->readAll()).object().toVariantMap();
        emit searchResultDetailChanged();
    });
}

void SearchClient::exportSearchReport(int taskId)
{
    if (taskId <= 0) {
        emit reportExported(false, "请先完成一次检索");
        return;
    }
    QNetworkReply *reply = m_manager->get(authRequest("/api/v1/search/tasks/" + QString::number(taskId) + "/report?format=csv"));
    connect(reply, &QNetworkReply::finished, this, [this, taskId, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit reportExported(false, apiErrorMessage(reply, "导出报告失败"));
            return;
        }

        QString downloadDir = QStandardPaths::writableLocation(QStandardPaths::DownloadLocation);
        if (downloadDir.isEmpty()) {
            downloadDir = QDir::homePath();
        }
        QDir().mkpath(downloadDir);
        const QString timestamp = QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss");
        const QString path = QDir(downloadDir).filePath(QString("myreid_search_report_%1_%2.csv").arg(taskId).arg(timestamp));
        QSaveFile file(path);
        if (!file.open(QIODevice::WriteOnly)) {
            emit reportExported(false, "无法写入下载目录");
            return;
        }
        file.write(reply->readAll());
        if (!file.commit()) {
            emit reportExported(false, "报告保存失败");
            return;
        }
        emit reportExported(true, "检索报告已导出：" + path);
    });
}

QString SearchClient::suggestedDownloadFileUrl(const QString &suggestedName) const
{
    QDir().mkpath(defaultDownloadDirectory());
    return QUrl::fromLocalFile(uniqueDownloadPath(defaultDownloadDirectory(), suggestedName)).toString();
}

void SearchClient::downloadFile(const QString &source, const QString &suggestedName, const QString &targetFile)
{
    QString trimmedSource = source.trimmed();
    if (trimmedSource.isEmpty()) {
        emit fileDownloaded(false, "没有可下载的文件地址");
        return;
    }

    QUrl url(trimmedSource);
    if (url.isRelative()) {
        if (!trimmedSource.startsWith('/')) {
            trimmedSource.prepend('/');
        }
        url = QUrl(m_baseUrl + trimmedSource);
    }
    if (!url.isValid() || url.scheme().isEmpty()) {
        emit fileDownloaded(false, "文件地址无效");
        return;
    }

    QNetworkRequest request(url);
    setupAuthHeaders(request);
    QNetworkReply *reply = m_manager->get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply, url, suggestedName, targetFile]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            emit fileDownloaded(false, apiErrorMessage(reply, "下载失败"));
            return;
        }

        QString fileName = suggestedName.trimmed();
        if (fileName.isEmpty()) {
            fileName = fileNameFromContentDisposition(reply->rawHeader("Content-Disposition"));
        }
        if (fileName.isEmpty()) {
            fileName = url.fileName(QUrl::FullyDecoded);
        }
        if (fileName.isEmpty()) {
            fileName = QStringLiteral("myreid_media_%1").arg(QDateTime::currentDateTime().toString("yyyyMMdd_HHmmss"));
        }

        QString targetPath = localPathFromDialogSelection(targetFile, fileName);
        if (targetPath.isEmpty()) {
            QDir().mkpath(defaultDownloadDirectory());
            targetPath = uniqueDownloadPath(defaultDownloadDirectory(), fileName);
        } else {
            QFileInfo targetInfo(targetPath);
            if (!QDir().mkpath(targetInfo.absolutePath())) {
                emit fileDownloaded(false, "无法创建保存目录");
                return;
            }
        }

        QSaveFile file(targetPath);
        if (!file.open(QIODevice::WriteOnly)) {
            emit fileDownloaded(false, "无法写入保存位置");
            return;
        }
        file.write(reply->readAll());
        if (!file.commit()) {
            emit fileDownloaded(false, "文件保存失败");
            return;
        }
        emit fileDownloaded(true, "文件已保存：" + targetPath);
    });
}

void SearchClient::handleNetworkReply(QNetworkReply *reply) {
    // 如果是 403 错误，说明访客限流触发了
    if (reply->error() == QNetworkReply::ContentAccessDenied || reply->error() == QNetworkReply::ProtocolInvalidOperationError) {
        QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
        QString detail = doc.object()["detail"].toString();
        emit errorOccurred(detail.isEmpty() ? "访问被拒绝" : detail);
        return;
    }

    if (reply->error() != QNetworkReply::NoError) {
        emit errorOccurred(apiErrorMessage(reply, "检索请求失败"));
        // 重新检查状态，更新UI
        checkInitialStatus();
        return;
    }

    QByteArray response = reply->readAll();
    QJsonDocument jsonDoc = QJsonDocument::fromJson(response);
    QJsonObject jsonObj = jsonDoc.object();

    if (jsonObj["status"].toString() == "success") {
        updateSearchPagination(jsonObj);
        QJsonArray resultsArray = jsonObj["results"].toArray();
        QVariantList resultList;
        for (const QJsonValue &value : resultsArray) {
            resultList.append(value.toObject().toVariantMap());
        }
        emit searchFinished(resultList);
        fetchHistory(); // 每次搜索成功后，自动刷新历史记录侧边栏
        checkInitialStatus();
    }
}
