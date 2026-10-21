#ifndef SEARCHCLIENT_H
#define SEARCHCLIENT_H

#include <QObject>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QVariantList>
#include <QVariantMap>
#include <QUuid> // 引入 UUID 库
#include <QTimer>
#include <QPointer>
#include <QSettings>
class SearchClient : public QObject
{
    Q_OBJECT
    // 暴露给 QML 的属性，用于判断界面状态
    Q_PROPERTY(bool isLoggedIn READ isLoggedIn NOTIFY loginStateChanged)
    Q_PROPERTY(QString currentUser READ currentUser NOTIFY loginStateChanged)
    Q_PROPERTY(QString currentUserId READ currentUserId NOTIFY userProfileChanged)
    Q_PROPERTY(QString currentEmail READ currentEmail NOTIFY userProfileChanged)
    Q_PROPERTY(QString currentAvatarUrl READ currentAvatarUrl NOTIFY userProfileChanged)
    Q_PROPERTY(QString currentRole READ currentRole NOTIFY userProfileChanged)
    Q_PROPERTY(QString currentCreatedAt READ currentCreatedAt NOTIFY userProfileChanged)
    Q_PROPERTY(QString enterpriseStatus READ enterpriseStatus NOTIFY enterpriseStatusChanged)
    Q_PROPERTY(QString backendBaseUrl READ backendBaseUrl WRITE setBackendBaseUrl NOTIFY backendBaseUrlChanged)
    Q_PROPERTY(QVariantList projectList READ projectList NOTIFY projectListChanged)
    Q_PROPERTY(QVariantList mediaList READ mediaList NOTIFY mediaListChanged)
    Q_PROPERTY(QVariantList jobList READ jobList NOTIFY jobListChanged)
    Q_PROPERTY(QVariantList auditList READ auditList NOTIFY auditListChanged)
    Q_PROPERTY(QVariantList adminUserList READ adminUserList NOTIFY adminUserListChanged)
    Q_PROPERTY(QVariantList availableCameraList READ availableCameraList NOTIFY availableCameraListChanged)
    Q_PROPERTY(QVariantMap scenicDashboard READ scenicDashboard NOTIFY scenicDashboardChanged)
    Q_PROPERTY(QVariantList scenicCameraPointList READ scenicCameraPointList NOTIFY scenicCameraPointListChanged)
    Q_PROPERTY(QVariantList scenicCameraWall READ scenicCameraWall NOTIFY scenicCameraWallChanged)
    Q_PROPERTY(QVariantList touristSessionList READ touristSessionList NOTIFY touristSessionListChanged)
    Q_PROPERTY(QVariantMap selectedTouristSession READ selectedTouristSession NOTIFY selectedTouristSessionChanged)
    Q_PROPERTY(QVariantMap searchResultDetail READ searchResultDetail NOTIFY searchResultDetailChanged)
    Q_PROPERTY(int searchPage READ searchPage NOTIFY searchPaginationChanged)
    Q_PROPERTY(int searchPageSize READ searchPageSize NOTIFY searchPaginationChanged)
    Q_PROPERTY(int searchTotal READ searchTotal NOTIFY searchPaginationChanged)
    Q_PROPERTY(int searchTotalPages READ searchTotalPages NOTIFY searchPaginationChanged)
    Q_PROPERTY(bool searchHasPrevious READ searchHasPrevious NOTIFY searchPaginationChanged)
    Q_PROPERTY(bool searchHasNext READ searchHasNext NOTIFY searchPaginationChanged)
    Q_PROPERTY(int lastSearchTaskId READ lastSearchTaskId NOTIFY searchPaginationChanged)
    Q_PROPERTY(QVariantList historyList READ historyList NOTIFY historyListChanged)
    Q_PROPERTY(QVariantList previewList READ previewList NOTIFY previewListChanged)
    Q_PROPERTY(QString currentSessionId READ currentSessionId NOTIFY sessionChanged)
    Q_PROPERTY(int currentProjectId READ currentProjectId NOTIFY currentProjectChanged)
    Q_PROPERTY(QString currentProjectName READ currentProjectName NOTIFY currentProjectChanged)
    Q_PROPERTY(QString currentProjectType READ currentProjectType NOTIFY currentProjectChanged)
    Q_PROPERTY(bool cameraRunning READ cameraRunning NOTIFY cameraStatusChanged)
    Q_PROPERTY(bool cameraMonitorOnline READ cameraMonitorOnline NOTIFY cameraStatusChanged)
    Q_PROPERTY(QString cameraFrameUrl READ cameraFrameUrl NOTIFY cameraStatusChanged)
    Q_PROPERTY(int cameraFrameVersion READ cameraFrameVersion NOTIFY cameraStatusChanged)
    Q_PROPERTY(int cameraTargetCount READ cameraTargetCount NOTIFY cameraStatusChanged)
    Q_PROPERTY(QString cameraMessage READ cameraMessage NOTIFY cameraStatusChanged)
    Q_PROPERTY(QString cameraSource READ cameraSource NOTIFY cameraStatusChanged)
    Q_PROPERTY(QString cameraRecordingUrl READ cameraRecordingUrl NOTIFY cameraStatusChanged)
    Q_PROPERTY(QString cameraRecordingName READ cameraRecordingName NOTIFY cameraStatusChanged)


public:
    explicit SearchClient(QObject *parent = nullptr);
    explicit SearchClient(const QString &systemScope, QObject *parent = nullptr);
    ~SearchClient() override;

    Q_INVOKABLE void searchByText(const QString &text);
    Q_INVOKABLE void searchByImage(const QString &fileUrl);
    Q_INVOKABLE void searchByTextAdvanced(const QString &text, double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId, int page, int pageSize);
    Q_INVOKABLE void searchByImageAdvanced(const QString &fileUrl, double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId, int page, int pageSize);
    Q_INVOKABLE void fetchSearchTaskPage(int taskId, int page, int pageSize);
    Q_INVOKABLE void fetchSearchResultDetail(int resultId);
    Q_INVOKABLE void exportSearchReport(int taskId);
    Q_INVOKABLE QString suggestedDownloadFileUrl(const QString &suggestedName = QString()) const;
    Q_INVOKABLE void downloadFile(const QString &source, const QString &suggestedName = QString(), const QString &targetFile = QString());
    Q_INVOKABLE void checkInitialStatus();
    Q_INVOKABLE void checkEnterpriseReadiness();

    // 鉴权
    Q_INVOKABLE void login(const QString &account, const QString &password);
    Q_INVOKABLE void requestEmailCode(const QString &email, const QString &purpose, const QString &username);
    Q_INVOKABLE void loginWithEmailCode(const QString &email, const QString &code);
    Q_INVOKABLE void registerUser(const QString &username, const QString &email, const QString &password, const QString &code);
    Q_INVOKABLE void logout();
    Q_INVOKABLE void restoreSession();
    Q_INVOKABLE void fetchUserProfile();
    Q_INVOKABLE void uploadAvatar(const QString &fileUrl);
    Q_INVOKABLE void changePassword(const QString &currentPassword, const QString &newPassword);
    Q_INVOKABLE void deactivateAccount();
    Q_INVOKABLE void fetchAdminUsers();
    Q_INVOKABLE void createAdminUser(const QString &username, const QString &email, const QString &password, const QString &role);
    Q_INVOKABLE void updateAdminUserRole(const QString &username, const QString &role);
    Q_INVOKABLE void setAdminUserActive(const QString &username, bool isActive);
    Q_INVOKABLE void resetAdminUserPassword(const QString &username, const QString &newPassword);
    Q_INVOKABLE void revokeAdminUserTokens(const QString &username);

    Q_INVOKABLE void uploadGallery(const QVariantList &fileUrls, bool isAppend = false);
    Q_INVOKABLE void uploadVideo(const QString &fileUrl, bool isAppend = false);
    // 历史记录与会话清理
    Q_INVOKABLE void fetchHistory();
    Q_INVOKABLE void cleanupSession();
    Q_INVOKABLE void createNewSession(); // 丢弃旧库，开辟新库
    Q_INVOKABLE void switchSession(const QString &sessionId); // 切换到旧库
    Q_INVOKABLE void deleteHistory(int historyId); // 删除历史
    Q_INVOKABLE void fetchPreview(); // 获取预览
    Q_INVOKABLE void fetchProjects();
    Q_INVOKABLE void selectProject(int projectId);
    Q_INVOKABLE void createProject(const QString &name, const QString &description);
    Q_INVOKABLE void createCameraProject(const QString &name, const QString &description, const QString &cameraSource);
    Q_INVOKABLE void deleteProject(int projectId);
    Q_INVOKABLE void fetchMedia();
    Q_INVOKABLE void deleteMedia(int mediaId);
    Q_INVOKABLE void clearProjectMedia();
    Q_INVOKABLE void fetchJobs();
    Q_INVOKABLE void fetchAudit();
    Q_INVOKABLE void fetchCameras();
    Q_INVOKABLE void fetchScenicDashboard();
    Q_INVOKABLE void fetchScenicCameraPoints();
    Q_INVOKABLE void fetchScenicCameraWall();
    Q_INVOKABLE void startScenicMonitoring();
    Q_INVOKABLE void stopScenicMonitoring();
    Q_INVOKABLE void syncScenicCameraPoints();
    Q_INVOKABLE void createScenicCameraPoint(const QString &name, const QString &cameraSource, const QString &role);
    Q_INVOKABLE void startScenicCamera(const QString &cameraSource = "0");
    Q_INVOKABLE void stopScenicCamera();
    Q_INVOKABLE void fetchScenicCameraStatus();
    Q_INVOKABLE void takeScenicCameraSnapshot();
    Q_INVOKABLE void uploadScenicSimulationVideo(int pointId, const QString &fileUrl);
    Q_INVOKABLE void registerTourist(const QString &fileUrl, const QString &ticketCode, const QString &visitorLabel, const QString &contactHint);
    Q_INVOKABLE void fetchTourists();
    Q_INVOKABLE void selectTouristSession(int sessionId);
    Q_INVOKABLE void matchTourist(int sessionId);
    Q_INVOKABLE void updateTouristSegment(int sessionId, int segmentId, bool included, int sortOrder);
    Q_INVOKABLE void updateTouristSegments(int sessionId, const QVariantList &segmentIds, bool included);
    Q_INVOKABLE void generateTouristVlog(int sessionId);
    Q_INVOKABLE void createScenicOrder(int sessionId, int amountCents);
    Q_INVOKABLE void createWechatScenicPayment(int sessionId, int amountCents);
    Q_INVOKABLE void refreshScenicOrderPayment(int orderId);
    Q_INVOKABLE void markScenicOrderPaid(int orderId);
    Q_INVOKABLE void refreshWorkspace();
    QVariantList historyList() const { return m_historyList; }
    QVariantList previewList() const { return m_previewList; }
    QVariantList projectList() const { return m_projectList; }
    QVariantList mediaList() const { return m_mediaList; }
    QVariantList jobList() const { return m_jobList; }
    QVariantList auditList() const { return m_auditList; }
    QVariantList adminUserList() const { return m_adminUserList; }
    QVariantList availableCameraList() const { return m_availableCameraList; }
    QVariantMap scenicDashboard() const { return m_scenicDashboard; }
    QVariantList scenicCameraPointList() const { return m_scenicCameraPointList; }
    QVariantList scenicCameraWall() const { return m_scenicCameraWall; }
    QVariantList touristSessionList() const { return m_touristSessionList; }
    QVariantMap selectedTouristSession() const { return m_selectedTouristSession; }
    QVariantMap searchResultDetail() const { return m_searchResultDetail; }
    int searchPage() const { return m_searchPage; }
    int searchPageSize() const { return m_searchPageSize; }
    int searchTotal() const { return m_searchTotal; }
    int searchTotalPages() const { return m_searchTotalPages; }
    bool searchHasPrevious() const { return m_searchHasPrevious; }
    bool searchHasNext() const { return m_searchHasNext; }
    int lastSearchTaskId() const { return m_lastSearchTaskId; }
    QString currentSessionId() const { return m_currentSessionId; }
    int currentProjectId() const { return m_currentProjectId; }
    QString currentProjectName() const { return m_currentProjectName; }
    QString currentProjectType() const { return m_currentProjectType; }
    bool isLoggedIn() const { return !m_token.isEmpty(); }
    QString currentUser() const { return m_currentUser; }
    QString currentUserId() const { return m_currentUserId; }
    QString currentEmail() const { return m_currentEmail; }
    QString currentAvatarUrl() const { return m_currentAvatarUrl; }
    QString currentRole() const { return m_currentRole; }
    QString currentCreatedAt() const { return m_currentCreatedAt; }
    QString enterpriseStatus() const { return m_enterpriseStatus; }
    QString backendBaseUrl() const { return m_baseUrl; }
    bool cameraRunning() const { return m_cameraRunning; }
    bool cameraMonitorOnline() const { return m_cameraMonitorOnline; }
    QString cameraFrameUrl() const { return m_cameraFrameUrl; }
    int cameraFrameVersion() const { return m_cameraFrameVersion; }
    int cameraTargetCount() const { return m_cameraTargetCount; }
    QString cameraMessage() const { return m_cameraMessage; }
    QString cameraSource() const { return m_cameraSource; }
    QString cameraRecordingUrl() const { return m_cameraRecordingUrl; }
    QString cameraRecordingName() const { return m_cameraRecordingName; }

    Q_INVOKABLE void startCamera(const QString &cameraSource = "0");
    Q_INVOKABLE void stopCamera();
    Q_INVOKABLE void fetchCameraStatus();
    Q_INVOKABLE void setBackendBaseUrl(const QString &baseUrl);

signals:
    void searchFinished(QVariantList results);
    void errorOccurred(QString errorMsg);
    void systemStatusChanged(bool isReady);
    void progressUpdated(int current, int total);
    void historyListChanged();
    // 鉴权信号
    void loginStateChanged();
    void authFinished(bool success, QString message);
    void emailCodeRequested(bool success, QString message);
    void userProfileChanged();
    void userOperationFinished(bool success, QString message);
    void profileUpdateFinished(bool success, QString message);
    void previewListChanged();
    void sessionChanged();
    void enterpriseStatusChanged();
    void backendBaseUrlChanged();
    void projectListChanged();
    void mediaListChanged();
    void jobListChanged();
    void auditListChanged();
    void adminUserListChanged();
    void adminUserOperationFinished(bool success, QString message);
    void availableCameraListChanged();
    void scenicDashboardChanged();
    void scenicCameraPointListChanged();
    void scenicCameraWallChanged();
    void touristSessionListChanged();
    void selectedTouristSessionChanged();
    void scenicOperationFinished(bool success, QString message);
    void currentProjectChanged();
    void cameraStatusChanged();
    void searchPaginationChanged();
    void scenicCameraSnapshotReady(QString url, QString filename);
    void searchResultDetailChanged();
    void reportExported(bool success, QString message);
    void fileDownloaded(bool success, QString message);

private:
    QNetworkAccessManager *m_manager;
    QString m_baseUrl = "http://127.0.0.1:2889";
    QTimer *m_statusTimer;
    QTimer *m_cameraTimer;
    QVariantList m_historyList;
    // 状态变量
    QString m_token;       // 登录后的 JWT Token
    QString m_currentUser; // 当前用户名
    QString m_currentUserId;
    QString m_currentEmail;
    QString m_currentAvatarUrl;
    QString m_currentRole;
    QString m_currentCreatedAt;
    QString m_guestId;     // 访客 UUID
    QString m_currentSessionId;
    QString m_refreshToken;
    int m_currentProjectId = 0;
    QString m_currentProjectName;
    QString m_currentProjectType = "media";
    QVariantList m_previewList;
    QVariantList m_projectList;
    QVariantList m_mediaList;
    QVariantList m_jobList;
    QVariantList m_auditList;
    QVariantList m_adminUserList;
    QVariantList m_availableCameraList;
    QVariantMap m_scenicDashboard;
    QVariantList m_scenicCameraPointList;
    QVariantList m_scenicCameraWall;
    QVariantList m_touristSessionList;
    QVariantMap m_selectedTouristSession;
    QVariantMap m_searchResultDetail;
    QString m_enterpriseStatus = "系统接口检测中";
    QString m_systemScope = "core";
    bool m_autoCreateDefaultProject = false;
    bool m_defaultProjectCreateInFlight = false;
    bool m_cameraRunning = false;
    bool m_cameraMonitorOnline = false;
    QString m_cameraFrameUrl;
    int m_cameraFrameVersion = 0;
    int m_cameraTargetCount = 0;
    QString m_cameraMessage = "实时相机未启动";
    QString m_cameraSource = "0";
    QString m_cameraRecordingUrl;
    QString m_cameraRecordingName;
    int m_searchPage = 1;
    int m_searchPageSize = 12;
    int m_searchTotal = 0;
    int m_searchTotalPages = 0;
    bool m_searchHasPrevious = false;
    bool m_searchHasNext = false;
    int m_lastSearchTaskId = 0;
    int m_scenicMonitoringStartedProjectId = 0;
    bool m_scenicMonitoringStartInFlight = false;
    quint64 m_authGeneration = 0;
    quint64 m_projectGeneration = 0;
    quint64 m_touristSelectionGeneration = 0;
    quint64 m_searchGeneration = 0;
    QPointer<QNetworkReply> m_refreshReply;
    QPointer<QNetworkReply> m_statusReply;
    QPointer<QNetworkReply> m_cameraReply;
    QPointer<QNetworkReply> m_readinessReply;
    QPointer<QNetworkReply> m_searchReply;
    QPointer<QNetworkReply> m_scenicMonitoringReply;

    // 统一请求构造
    void setupAuthHeaders(QNetworkRequest &request);
    QNetworkRequest jsonRequest(const QString &path);
    QNetworkRequest authRequest(const QString &path);
    QString apiPath(const QString &path) const;
    QString settingsPrefix() const;
    bool isScenicScope() const;
    void fetchScenicWorkspace();
    void fetchCoreWorkspace();
    void persistSession();
    void clearPersistedSession();
    void clearLocalSessionState();
    void setCurrentProject(const QJsonObject &project);
    void updateUserProfile(const QJsonObject &profile);
    void refreshAccessToken();
    void ensureDefaultProject();
    void createDefaultProject();
    void updateCameraStatus(const QJsonObject &status);
    void updateScenicCameraWall(const QJsonArray &wall);
    void clearCameraStatus();
    QJsonObject buildSearchFilters(double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId) const;
    void appendSearchQueryFilters(QUrlQuery &query, double minSimilarity, const QString &cameraId, const QString &startTime, const QString &endTime, int mediaId, int page, int pageSize) const;
    void updateSearchPagination(const QJsonObject &response);

    void pollStatus();
    void handleNetworkReply(QNetworkReply *reply);


};

#endif // SEARCHCLIENT_H
