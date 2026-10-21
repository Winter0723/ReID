pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import QtQuick.Effects
import "components"

Window {
    id: mainWindow
    readonly property int sidebarWidth: 248
    readonly property int contentOuterMargin: 24
    readonly property int contentMinimumWidth: 1040
    readonly property int contentMaximumWidth: 1320

    width: 1440
    height: 860
    minimumWidth: sidebarWidth + contentMinimumWidth + contentOuterMargin * 2
    minimumHeight: 760
    visible: true
    title: "行人重识别系统"
    color: "#F8FAFC"

    property string appMode: "launcher"
    property string currentPage: "projects"
    property bool isSystemReady: false
    property bool isAppendMode: true
    property string confirmAction: ""
    property int confirmTargetId: 0
    property string confirmTargetName: ""
    property bool searchRefreshing: false
    property string searchRefreshMessage: ""
    property real searchRefreshProgress: 0
    property real searchMinSimilarity: 0
    property string searchCameraFilter: ""
    property string searchStartTimeFilter: ""
    property string searchEndTimeFilter: ""
    property int searchMediaFilter: 0
    property string lastSearchMode: ""
    property string lastSearchText: ""
    property string lastSearchImageUrl: ""
    property string previewTitle: ""
    property string previewType: "image"
    property string previewSource: ""
    property string previewDownloadSource: ""
    property string previewDownloadName: ""
    property string previewFrameSource: ""
    property real previewSeekSeconds: 0
    property int previewSeekAttempts: 0
    property string pendingDownloadSource: ""
    property string pendingDownloadName: ""
    ListModel { id: resultModel }

    function absoluteUrl(path) {
        if (!path)
            return ""
        var raw = String(path)
        if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw))
            return raw
        if (raw.indexOf("./gallery/") === 0)
            raw = raw.substring(1)
        else if (raw.indexOf("gallery/") === 0)
            raw = "/" + raw
        else if (raw.indexOf("./object_storage/") === 0)
            raw = "/objects/" + raw.substring("./object_storage/".length)
        else if (raw.indexOf("object_storage/") === 0)
            raw = "/objects/" + raw.substring("object_storage/".length)
        else if (raw.indexOf("projects/") === 0)
            raw = "/objects/" + raw
        return raw.indexOf("/") === 0 ? "http://127.0.0.1:2889" + encodeURI(raw) : encodeURI(raw)
    }

    function fileNameFromPath(path, fallbackName) {
        var raw = String(path || "")
        var clean = raw.split("#")[0].split("?")[0]
        var slash = clean.lastIndexOf("/")
        var name = slash >= 0 ? clean.substring(slash + 1) : clean
        try {
            name = decodeURIComponent(name)
        } catch (err) {
        }
        name = name.replace(/[\\/:*?"<>|]/g, "_").trim()
        return name.length > 0 ? name : (fallbackName || "myreid_media")
    }

    function fileSuffix(fileName) {
        var name = fileNameFromPath(fileName, "")
        var dot = name.lastIndexOf(".")
        return dot > 0 && dot < name.length - 1 ? name.substring(dot + 1) : ""
    }

    function downloadNameFilters(fileName) {
        var suffix = fileSuffix(fileName).toLowerCase()
        if (["jpg", "jpeg", "png", "webp", "bmp"].indexOf(suffix) >= 0)
            return ["图片文件 (*.jpg *.jpeg *.png *.webp *.bmp)", "所有文件 (*)"]
        if (["mp4", "avi", "mov", "mkv"].indexOf(suffix) >= 0)
            return ["视频文件 (*.mp4 *.avi *.mov *.mkv)", "所有文件 (*)"]
        return ["所有文件 (*)"]
    }

    function requestDownload(source, suggestedName) {
        var resolvedSource = absoluteUrl(source)
        if (resolvedSource.length === 0) {
            messageDialog.message = "没有可下载的文件地址"
            messageDialog.open()
            return
        }
        var name = fileNameFromPath(suggestedName || source, "myreid_media")
        pendingDownloadSource = resolvedSource
        pendingDownloadName = name
        downloadDialog.nameFilters = downloadNameFilters(name)
        downloadDialog.defaultSuffix = fileSuffix(name)
        downloadDialog.currentFile = searchClient.suggestedDownloadFileUrl(name)
        downloadDialog.open()
    }

    function timestampToSeconds(value) {
        if (value === undefined || value === null || value === "")
            return 0
        if (typeof value === "number")
            return Math.max(0, value)
        var text = String(value).trim()
        if (text.length === 0)
            return 0
        if (/^\d+(\.\d+)?$/.test(text))
            return Math.max(0, Number(text))
        var parts = text.split(":")
        if (parts.length === 3)
            return Math.max(0, Number(parts[0]) * 3600 + Number(parts[1]) * 60 + Number(parts[2]))
        if (parts.length === 2)
            return Math.max(0, Number(parts[0]) * 60 + Number(parts[1]))
        return 0
    }

    function formatMediaTime(ms) {
        var totalSeconds = Math.max(0, Math.floor(Number(ms || 0) / 1000))
        var hours = Math.floor(totalSeconds / 3600)
        var minutes = Math.floor((totalSeconds % 3600) / 60)
        var seconds = totalSeconds % 60
        var mm = minutes < 10 ? "0" + minutes : String(minutes)
        var ss = seconds < 10 ? "0" + seconds : String(seconds)
        return hours > 0 ? hours + ":" + mm + ":" + ss : mm + ":" + ss
    }

    function previewMedia(mediaType, title, source, seekSeconds, downloadSource, downloadName, frameSource) {
        var resolvedSource = absoluteUrl(source)
        if (resolvedSource.length === 0) {
            messageDialog.message = "没有可预览的媒体地址"
            messageDialog.open()
            return
        }
        var sourceLower = resolvedSource.toLowerCase()
        var isVideoSource = mediaType === "video"
                || mediaType === "camera"
                || sourceLower.indexOf(".mp4") >= 0
                || sourceLower.indexOf(".mov") >= 0
                || sourceLower.indexOf(".avi") >= 0
                || sourceLower.indexOf(".mkv") >= 0
        previewType = isVideoSource ? "video" : "image"
        previewTitle = title || (previewType === "video" ? "视频预览" : "图片预览")
        previewSource = resolvedSource
        previewDownloadSource = absoluteUrl(downloadSource || source)
        previewDownloadName = downloadName || fileNameFromPath(previewDownloadSource || previewSource, "myreid_media")
        previewFrameSource = absoluteUrl(frameSource || "")
        previewSeekSeconds = Math.max(0, Number(seekSeconds || 0))
        previewSeekAttempts = 0
        mediaPreviewDialog.open()
        if (previewType === "video") {
            previewPlayer.stop()
            previewPlayer.source = previewSource
            previewPlayer.play()
            if (previewSeekSeconds > 0)
                previewSeekTimer.start()
        }
    }

    function applyPreviewSeek() {
        if (previewType !== "video" || previewSeekSeconds <= 0 || previewPlayer.source.toString().length === 0)
            return
        previewPlayer.position = Math.round(previewSeekSeconds * 1000)
    }

    function updateSearchResults(results) {
        resultModel.clear()
        for (var i = 0; i < results.length; i++) {
            resultModel.append({
                "imageUrl": absoluteUrl(results[i].path),
                "result_id": results[i].id || 0,
                "rank": results[i].rank || (i + 1),
                "similarity": results[i].similarity || 0,
                "source_info": results[i].source_info || "",
                "media_id": results[i].media_id || "",
                "media_filename": results[i].media_filename || "",
                "media_type": results[i].media_type || "",
                "camera_id": results[i].camera_id || "",
                "timestamp": results[i].timestamp || "",
                "original_media_url": absoluteUrl(results[i].original_media_url || results[i].original_media_path || ""),
                "original_media_path": results[i].original_media_path || "",
                "video_seek_seconds": results[i].video_seek_seconds || 0
            })
        }
    }

    function startSearchRefresh(modeLabel) {
        searchRefreshMessage = modeLabel + "检索中，正在刷新结果"
        searchRefreshProgress = 0.08
        searchRefreshing = true
        resultModel.clear()
        currentPage = "search"
    }

    function finishSearchRefresh() {
        searchRefreshProgress = 1
        searchRefreshing = false
    }

    function submitTextSearch() {
        var query = contentArea.searchText.trim()
        if (query.length === 0) {
            messageDialog.message = "请输入检索文本"
            messageDialog.open()
            return
        }
        lastSearchMode = "text"
        lastSearchText = query
        startSearchRefresh("以文搜图")
        searchClient.searchByTextAdvanced(query, searchMinSimilarity, searchCameraFilter, searchStartTimeFilter, searchEndTimeFilter, searchMediaFilter, 1, 12)
    }

    function submitImageSearch(fileUrl) {
        contentArea.clearSearchInput()
        lastSearchMode = "image"
        lastSearchImageUrl = fileUrl
        startSearchRefresh("以图搜图")
        searchClient.searchByImageAdvanced(fileUrl, searchMinSimilarity, searchCameraFilter, searchStartTimeFilter, searchEndTimeFilter, searchMediaFilter, 1, 12)
    }

    function requestSearchPage(page) {
        if (searchClient.lastSearchTaskId > 0) {
            startSearchRefresh("检索结果")
            searchClient.fetchSearchTaskPage(searchClient.lastSearchTaskId, page, 12)
        }
    }

    function selectHistoryItem(item) {
        searchClient.selectProject(item.project_id)
        lastSearchMode = item.type
        lastSearchText = item.type === "text" ? item.content : ""
        lastSearchImageUrl = ""
        contentArea.setSearchText(item.type === "text" ? item.content : "")
        startSearchRefresh("历史记录")
        searchClient.fetchSearchTaskPage(item.id, 1, 12)
        currentPage = "search"
    }

    function formatBytes(value) {
        var size = Number(value || 0)
        if (size < 1024)
            return size + " B"
        if (size < 1024 * 1024)
            return (size / 1024).toFixed(1) + " KB"
        return (size / 1024 / 1024).toFixed(1) + " MB"
    }

    function mediaTypeLabel(value) {
        if (value === "camera")
            return "相机"
        if (value === "video")
            return "视频"
        if (value === "image")
            return "图片"
        return value || "-"
    }

    function statusLabel(value) {
        if (value === "registered")
            return "已入园"
        if (value === "matched")
            return "已匹配"
        if (value === "no_match")
            return "未命中"
        if (value === "rendering")
            return "成片中"
        if (value === "vlog_ready")
            return "可售卖"
        if (value === "vlog_failed")
            return "成片失败"
        if (value === "paid")
            return "已支付"
        if (value === "pending")
            return "待支付"
        if (value === "succeeded")
            return "成功"
        if (value === "streaming")
            return "采集中"
        if (value === "stopped")
            return "已停止"
        if (value === "indexed")
            return "已索引"
        if (value === "processing")
            return "处理中"
        if (value === "uploaded")
            return "已上传"
        if (value === "failed")
            return "失败"
        return value || "-"
    }

    function projectTypeLabel(value) {
        if (value === "camera")
            return "实时相机"
        return "媒体项目"
    }

    function isCameraProject() {
        return searchClient.currentProjectType === "camera"
    }

    function navIcon(key) {
        if (key === "projects")
            return "项"
        if (key === "scenic")
            return "游"
        if (key === "detail")
            return "详"
        if (key === "media")
            return "媒"
        if (key === "camera")
            return "相"
        if (key === "jobs")
            return "任"
        if (key === "search")
            return "搜"
        if (key === "history")
            return "历"
        if (key === "users")
            return "用"
        return "审"
    }

    function jobProgress(value) {
        var progress = Number(value || 0)
        return progress > 1 ? progress / 100 : progress
    }

    function accountInitial() {
        var name = searchClient.currentUser || "U"
        return name.length > 0 ? name.charAt(0).toUpperCase() : "U"
    }

    function roleLabel(value) {
        if (value === "admin")
            return "管理员"
        if (value === "operator")
            return "操作员"
        if (value === "viewer")
            return "观察员"
        return value || "-"
    }

    function adminRoleIndex(value) {
        if (value === "admin")
            return 0
        if (value === "operator")
            return 1
        if (value === "viewer")
            return 2
        return 1
    }

    function activeAdminUserCount() {
        var count = 0
        for (var i = 0; i < searchClient.adminUserList.length; i++) {
            var item = searchClient.adminUserList[i]
            if (item.role === "admin" && item.is_active)
                count += 1
        }
        return count
    }

    function activeUserCount() {
        var count = 0
        for (var i = 0; i < searchClient.adminUserList.length; i++) {
            if (searchClient.adminUserList[i].is_active)
                count += 1
        }
        return count
    }

    function openConfirm(action, targetId, dialogTitle, dialogMessage, targetName) {
        confirmAction = action
        confirmTargetId = targetId || 0
        confirmTargetName = targetName || ""
        confirmDialog.title = dialogTitle
        confirmDialog.message = dialogMessage
        confirmDialog.open()
    }

    onClosing: close => searchClient.cleanupSession()


    function openGalleryDialog() {
        isAppendMode = true
        galleryDialog.open()
    }

    function openVideoDialog() {
        isAppendMode = true
        videoDialog.open()
    }

    function openProjectDialog() {
        projectDialog.open()
    }

    function openQueryDialog() {
        queryDialog.open()
    }

    function openAuthDialog() {
        authDialog.open()
    }

    function openUserMenu() {
        userMenu.open()
    }

    function openAdminCreateUserDialog() {
        adminCreateUserDialog.open()
    }

    function openAdminResetPasswordDialog(username) {
        adminResetPasswordDialog.targetUsername = username
        adminResetPasswordDialog.open()
    }

    Component.onCompleted: {
        searchClient.checkEnterpriseReadiness()
        searchClient.refreshWorkspace()
    }

    Connections {
        target: searchClient

        function onSystemStatusChanged(ready) {
            isSystemReady = ready
            headerBar.statusMessage = ready ? "索引就绪" : (searchClient.isLoggedIn ? "等待媒体索引" : "请登录")
            headerBar.progressVisible = false
        }

        function onProgressUpdated(current, total) {
            headerBar.progressVisible = true
            headerBar.progressValue = total > 0 ? current / total : 0
            headerBar.statusMessage = total > 0 ? "索引中 " + current + " / " + total : "正在处理媒体"
        }

        function onErrorOccurred(errorMsg) {
            if (searchRefreshing)
                finishSearchRefresh()
            messageDialog.message = errorMsg
            messageDialog.open()
        }

        function onAuthFinished(success, message) {
            messageDialog.message = message
            messageDialog.open()
            if (success) {
                authDialog.registerMode = false
                authDialog.loginMode = "email_code"
                authDialog.close()
                currentPage = "projects"
            }
        }

        function onEmailCodeRequested(success, message) {
            messageDialog.message = message
            messageDialog.open()
        }

        function onUserOperationFinished(success, message) {
            messageDialog.message = message
            messageDialog.open()
            if (success) {
                userDialog.close()
            }
        }

        function onAdminUserOperationFinished(success, message) {
            messageDialog.message = message
            messageDialog.open()
            if (success) {
                adminCreateUserDialog.close()
                adminResetPasswordDialog.close()
            }
        }

        function onScenicOperationFinished(success, message) {
            messageDialog.message = message
            messageDialog.open()
        }

        function onUserProfileChanged() {
            if (searchClient.currentRole !== "admin" && currentPage === "users")
                currentPage = "projects"
        }

        function onProfileUpdateFinished(success, message) {
            messageDialog.message = message
            messageDialog.open()
        }

        function onSearchFinished(results) {
            finishSearchRefresh()
            updateSearchResults(results)
            currentPage = "search"
        }

        function onSearchResultDetailChanged() {
            if (searchClient.searchResultDetail && searchClient.searchResultDetail.id)
                resultDetailDialog.open()
        }

        function onReportExported(success, message) {
            messageDialog.message = message
            messageDialog.open()
        }

        function onFileDownloaded(success, message) {
            messageDialog.message = message
            messageDialog.open()
        }
    }

    NumberAnimation on searchRefreshProgress {
        from: 0.08
        to: 0.96
        duration: 900
        loops: Animation.Infinite
        running: searchRefreshing
    }

    Timer {
        interval: 3000
        repeat: true
        running: currentPage === "camera" && searchClient.isLoggedIn && isCameraProject() && !searchClient.cameraRunning
        onTriggered: searchClient.fetchCameras()
    }

    Timer {
        interval: 3000
        repeat: true
        running: projectDialog.visible && projectDialog.projectMode === "camera" && searchClient.isLoggedIn
        onTriggered: searchClient.fetchCameras()
    }

    AppDialog {
        id: messageDialog
        title: "系统提示"
        anchors.centerIn: parent
        width: 380
        modal: true
        padding: 18
        property string message: ""

        ColumnLayout {
            anchors.fill: parent
            spacing: 16
            Text {
                text: messageDialog.message
                wrapMode: Text.WordWrap
                color: "#172033"
                font.pixelSize: 14
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                AppButton {
                    text: "知道了"
                    highlighted: true
                    onClicked: messageDialog.close()
                }
            }
        }
    }

    AppDialog {
        id: authDialog
        property bool registerMode: false
        property string loginMode: "email_code"
        title: registerMode ? "注册账号" : "登录账号"
        anchors.centerIn: parent
        width: 410
        modal: true
        padding: 18
        onOpened: {
            authPasswordField.clear()
            authCodeField.clear()
        }
        onClosed: {
            registerMode = false
            loginMode = "email_code"
            authAccountField.clear()
            authEmailField.clear()
            authPasswordField.clear()
            authCodeField.clear()
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            RowLayout {
                visible: !authDialog.registerMode
                Layout.fillWidth: true
                spacing: 8
                SegmentButton {
                    text: "邮箱验证码"
                    selected: authDialog.loginMode === "email_code"
                    onClicked: authDialog.loginMode = "email_code"
                }
                SegmentButton {
                    text: "密码登录"
                    selected: authDialog.loginMode === "password"
                    onClicked: authDialog.loginMode = "password"
                }
            }

            InputField {
                id: authAccountField
                placeholderText: authDialog.registerMode ? "用户名" : (authDialog.loginMode === "email_code" ? "邮箱" : "用户名或邮箱")
                Layout.fillWidth: true
            }

            InputField {
                id: authEmailField
                visible: authDialog.registerMode
                placeholderText: "邮箱"
                Layout.fillWidth: true
            }

            InputField {
                id: authPasswordField
                visible: authDialog.registerMode || authDialog.loginMode === "password"
                placeholderText: "密码"
                echoMode: TextInput.Password
                Layout.fillWidth: true
                onAccepted: {
                    if (authDialog.registerMode)
                        searchClient.registerUser(authAccountField.text, authEmailField.text, authPasswordField.text, authCodeField.text)
                    else if (authDialog.loginMode === "email_code")
                        searchClient.loginWithEmailCode(authAccountField.text, authCodeField.text)
                    else
                        searchClient.login(authAccountField.text, authPasswordField.text)
                }
            }

            RowLayout {
                visible: authDialog.registerMode || authDialog.loginMode === "email_code"
                Layout.fillWidth: true
                spacing: 8
                InputField {
                    id: authCodeField
                    placeholderText: "邮箱验证码"
                    Layout.fillWidth: true
                    onAccepted: {
                        if (authDialog.registerMode)
                            searchClient.registerUser(authAccountField.text, authEmailField.text, authPasswordField.text, authCodeField.text)
                        else
                            searchClient.loginWithEmailCode(authAccountField.text, authCodeField.text)
                    }
                }
                AppButton {
                    text: "发送验证码"
                    compact: true
                    onClicked: {
                        if (authDialog.registerMode)
                            searchClient.requestEmailCode(authEmailField.text, "register", authAccountField.text)
                        else
                            searchClient.requestEmailCode(authAccountField.text, "login", "")
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                AppButton {
                    text: authDialog.registerMode ? "创建账号" : (authDialog.loginMode === "email_code" ? "验证码登录" : "登录")
                    Layout.fillWidth: true
                    highlighted: true
                    onClicked: {
                        if (authDialog.registerMode)
                            searchClient.registerUser(authAccountField.text, authEmailField.text, authPasswordField.text, authCodeField.text)
                        else if (authDialog.loginMode === "email_code")
                            searchClient.loginWithEmailCode(authAccountField.text, authCodeField.text)
                        else
                            searchClient.login(authAccountField.text, authPasswordField.text)
                    }
                }
                AppButton {
                    text: authDialog.registerMode ? "返回登录" : "注册"
                    Layout.fillWidth: true
                    onClicked: {
                        authDialog.registerMode = !authDialog.registerMode
                        authPasswordField.clear()
                        authCodeField.clear()
                    }
                }
            }
            AppButton {
                text: "取消"
                subtle: true
                Layout.fillWidth: true
                onClicked: authDialog.close()
            }
        }
    }

    AppDialog {
        id: confirmDialog
        property string message: ""
        anchors.centerIn: parent
        width: 430
        modal: true
        padding: 18

        ColumnLayout {
            anchors.fill: parent
            spacing: 14
            Text {
                text: confirmDialog.message
                color: "#172033"
                font.pixelSize: 14
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Item { Layout.fillWidth: true }
                AppButton {
                    text: "取消"
                    subtle: true
                    onClicked: confirmDialog.close()
                }
                AppButton {
                    text: "确认"
                    danger: true
                    highlighted: true
                    onClicked: {
                        confirmDialog.close()
                        if (confirmAction === "deleteMedia")
                            searchClient.deleteMedia(confirmTargetId)
                        else if (confirmAction === "clearMedia") {
                            resultModel.clear()
                            searchClient.clearProjectMedia()
                        } else if (confirmAction === "deleteProject") {
                            if (confirmTargetId === searchClient.currentProjectId) {
                                resultModel.clear()
                                currentPage = "projects"
                            }
                            searchClient.deleteProject(confirmTargetId)
                        } else if (confirmAction === "deactivateAccount") {
                            searchClient.deactivateAccount()
                        } else if (confirmAction === "adminDisableUser") {
                            searchClient.setAdminUserActive(confirmTargetName, false)
                        } else if (confirmAction === "adminEnableUser") {
                            searchClient.setAdminUserActive(confirmTargetName, true)
                        } else if (confirmAction === "adminRevokeTokens") {
                            searchClient.revokeAdminUserTokens(confirmTargetName)
                        }
                    }
                }
            }
        }
    }

    AppDialog {
        id: resultDetailDialog
        title: "结果详情"
        anchors.centerIn: parent
        width: 620
        modal: true
        padding: 18

        RowLayout {
            anchors.fill: parent
            spacing: 14

            Rectangle {
                Layout.preferredWidth: 220
                Layout.preferredHeight: 260
                radius: 8
                color: "#edf2f7"
                clip: true
                Image {
                    anchors.fill: parent
                    source: absoluteUrl(searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path || "")
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 10
                ProfileRow { label: "排名"; value: "#" + String(searchClient.searchResultDetail.rank || "-") }
                ProfileRow { label: "相似度"; value: Number(searchClient.searchResultDetail.similarity || 0).toFixed(4) }
                ProfileRow { label: "媒体"; value: searchClient.searchResultDetail.media_filename || ("#" + (searchClient.searchResultDetail.media_id || "-")) }
                ProfileRow { label: "原文件"; value: absoluteUrl(searchClient.searchResultDetail.original_media_url || searchClient.searchResultDetail.original_media_path || "") || "-" }
                ProfileRow { label: "类型"; value: mediaTypeLabel(searchClient.searchResultDetail.media_type || "") }
                ProfileRow { label: "摄像头"; value: searchClient.searchResultDetail.camera_id || "-" }
                ProfileRow { label: "时间点"; value: searchClient.searchResultDetail.timestamp || "-" }
                Text {
                    text: searchClient.searchResultDetail.source_info || ""
                    color: "#5e6b7d"
                    font.pixelSize: 12
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                    maximumLineCount: 4
                    elide: Text.ElideRight
                }
                Flow {
                    Layout.fillWidth: true
                    spacing: 8
                    AppButton {
                        text: "预览命中图"
                        enabled: Boolean(searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path)
                        highlighted: true
                        onClicked: previewMedia("image", "命中图像 #" + (searchClient.searchResultDetail.rank || "-"), searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path, 0, searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path, "myreid_result_" + (searchClient.searchResultDetail.id || "frame") + ".jpg", "")
                    }
                    AppButton {
                        text: "下载命中图"
                        enabled: Boolean(searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path)
                        subtle: true
                        onClicked: requestDownload(searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path, "myreid_result_" + (searchClient.searchResultDetail.id || "frame") + ".jpg")
                    }
                    AppButton {
                        text: "预览原媒体"
                        enabled: Boolean(searchClient.searchResultDetail.original_media_url || searchClient.searchResultDetail.original_media_path)
                        onClicked: previewMedia(searchClient.searchResultDetail.media_type || "image", searchClient.searchResultDetail.media_filename || "原媒体", searchClient.searchResultDetail.original_media_url || searchClient.searchResultDetail.original_media_path, Number(searchClient.searchResultDetail.video_seek_seconds || 0), searchClient.searchResultDetail.original_media_url || searchClient.searchResultDetail.original_media_path, searchClient.searchResultDetail.media_filename || "", searchClient.searchResultDetail.thumbnail_url || searchClient.searchResultDetail.path || "")
                    }
                    AppButton {
                        text: "下载原媒体"
                        enabled: Boolean(searchClient.searchResultDetail.original_media_url || searchClient.searchResultDetail.original_media_path)
                        subtle: true
                        onClicked: requestDownload(searchClient.searchResultDetail.original_media_url || searchClient.searchResultDetail.original_media_path, searchClient.searchResultDetail.media_filename || "")
                    }
                }
            }
        }
    }

    Popup {
        id: mediaPreviewDialog
        x: Math.round((mainWindow.width - width) / 2)
        y: Math.round((mainWindow.height - height) / 2)
        width: Math.min(920, mainWindow.width - 72)
        height: Math.min(680, mainWindow.height - 72)
        modal: true
        focus: true
        padding: 0
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onClosed: {
            previewSeekTimer.stop()
            previewPlayer.stop()
            previewPlayer.source = ""
        }
        background: Rectangle {
            color: "#ffffff"
            radius: 10
            border.color: "#d8dee9"
        }

        AudioOutput {
            id: previewAudio
            volume: 1.0
        }

        MediaPlayer {
            id: previewPlayer
            audioOutput: previewAudio
            videoOutput: previewVideoOutput
            onMediaStatusChanged: {
                if (previewType === "video" && previewSeekSeconds > 0)
                    previewSeekTimer.start()
            }
        }

        Timer {
            id: previewSeekTimer
            interval: 260
            repeat: true
            onTriggered: {
                previewSeekAttempts += 1
                applyPreviewSeek()
                if (previewSeekAttempts >= 8)
                    stop()
            }
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                Layout.leftMargin: 18
                Layout.rightMargin: 12
                spacing: 12
                Text {
                    text: previewTitle
                    color: "#172033"
                    font.pixelSize: 16
                    font.bold: true
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                Text {
                    visible: previewType === "video" && previewSeekSeconds > 0
                    text: "定位 " + formatMediaTime(previewSeekSeconds * 1000)
                    color: "#657386"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                AppButton {
                    text: "关闭"
                    compact: true
                    subtle: true
                    onClicked: mediaPreviewDialog.close()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.leftMargin: 18
                Layout.rightMargin: 18
                color: "#101622"
                radius: 8
                clip: true

                Image {
                    id: previewImage
                    anchors.fill: parent
                    anchors.margins: 12
                    visible: previewType !== "video"
                    source: visible ? previewSource : ""
                    fillMode: Image.PreserveAspectFit
                    asynchronous: true
                }
                VideoOutput {
                    id: previewVideoOutput
                    anchors.fill: parent
                    anchors.margins: 12
                    visible: previewType === "video"
                    fillMode: VideoOutput.PreserveAspectFit
                }
                Text {
                    anchors.centerIn: parent
                    visible: previewType !== "video" && previewImage.status === Image.Error
                    text: "图片无法预览"
                    color: "#dbe5f3"
                    font.pixelSize: 13
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 18
                Layout.rightMargin: 18
                Layout.topMargin: 12
                Layout.bottomMargin: 16
                spacing: 10

                RowLayout {
                    visible: previewType === "video"
                    Layout.fillWidth: true
                    spacing: 10
                    AppButton {
                        text: previewPlayer.playbackState === MediaPlayer.PlayingState ? "暂停" : "播放"
                        compact: true
                        highlighted: previewPlayer.playbackState !== MediaPlayer.PlayingState
                        onClicked: {
                            if (previewPlayer.playbackState === MediaPlayer.PlayingState)
                                previewPlayer.pause()
                            else
                                previewPlayer.play()
                        }
                    }
                    Slider {
                        Layout.fillWidth: true
                        enabled: previewPlayer.duration > 0
                        from: 0
                        to: Math.max(1, previewPlayer.duration)
                        value: previewPlayer.position
                        onMoved: {
                            previewSeekTimer.stop()
                            previewPlayer.position = value
                        }
                    }
                    Text {
                        text: formatMediaTime(previewPlayer.position) + " / " + formatMediaTime(previewPlayer.duration)
                        color: "#46556a"
                        font.pixelSize: 12
                        Layout.preferredWidth: 116
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideRight
                    }
                }

                RowLayout {
                    visible: previewType === "video" && previewFrameSource.length > 0
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle {
                        Layout.preferredWidth: 76
                        Layout.preferredHeight: 56
                        radius: 7
                        color: "#edf2f7"
                        clip: true
                        Image {
                            anchors.fill: parent
                            source: previewFrameSource
                            fillMode: Image.PreserveAspectCrop
                            asynchronous: true
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: "命中帧"
                            color: "#172033"
                            font.pixelSize: 13
                            font.bold: true
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: previewSeekSeconds > 0 ? "时间点 " + formatMediaTime(previewSeekSeconds * 1000) : previewTitle
                            color: "#657386"
                            font.pixelSize: 12
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                    }
                    AppButton {
                        text: "下载命中帧"
                        compact: true
                        subtle: true
                        onClicked: requestDownload(previewFrameSource, "myreid_frame_" + Date.now() + ".jpg")
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Item { Layout.fillWidth: true }
                    AppButton {
                        text: previewType === "video" ? "下载视频" : "下载图片"
                        highlighted: true
                        enabled: previewDownloadSource.length > 0
                        onClicked: requestDownload(previewDownloadSource, previewDownloadName)
                    }
                }
            }
        }
    }

    Popup {
        id: userDialog
        property string activeTab: "profile"
        x: Math.round((mainWindow.width - width) / 2)
        y: Math.round((mainWindow.height - height) / 2)
        width: Math.min(760, mainWindow.width - 72)
        height: Math.min(520, mainWindow.height - 72)
        modal: true
        focus: true
        padding: 0
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        onOpened: {
            activeTab = "profile"
            searchClient.fetchUserProfile()
        }
        background: Rectangle {
            color: "#ffffff"
            radius: 10
            border.color: "#d8dee9"
        }

        contentItem: ColumnLayout {
            width: userDialog.width
            height: userDialog.height
            spacing: 0

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 58
                color: "#ffffff"
                radius: 10
                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                    color: "#e4eaf2"
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 20
                    anchors.rightMargin: 14
                    spacing: 12
                    Text {
                        text: "用户信息"
                        color: "#172033"
                        font.pixelSize: 18
                        font.bold: true
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    AppButton {
                        text: "关闭"
                        compact: true
                        subtle: true
                        onClicked: userDialog.close()
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    Layout.preferredWidth: 168
                    Layout.fillHeight: true
                    color: "#f8fafc"
                    border.color: "#e4eaf2"

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 8
                        AccountTab {
                            text: "资料展示"
                            selected: userDialog.activeTab === "profile"
                            onClicked: userDialog.activeTab = "profile"
                        }
                        AccountTab {
                            text: "修改密码"
                            selected: userDialog.activeTab === "password"
                            onClicked: userDialog.activeTab = "password"
                        }
                        AccountTab {
                            text: "注销用户"
                            selected: userDialog.activeTab === "deactivate"
                            danger: true
                            onClicked: userDialog.activeTab = "deactivate"
                        }
                        Item { Layout.fillHeight: true }
                    }
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: userDialog.activeTab === "password" ? 1 : (userDialog.activeTab === "deactivate" ? 2 : 0)

                    Item {
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 16

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 14
                                Rectangle {
                                    Layout.preferredWidth: 64
                                    Layout.preferredHeight: 64
                                    radius: 32
                                    color: "#2f6fed"
                                    clip: true
                                    Image {
                                        anchors.fill: parent
                                        source: searchClient.currentAvatarUrl
                                        fillMode: Image.PreserveAspectCrop
                                        visible: searchClient.currentAvatarUrl.length > 0
                                    }
                                    Text {
                                        anchors.centerIn: parent
                                        text: accountInitial()
                                        color: "white"
                                        font.pixelSize: 25
                                        font.bold: true
                                        visible: searchClient.currentAvatarUrl.length === 0
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 5
                                    Text {
                                        text: searchClient.currentUser || "-"
                                        color: "#172033"
                                        font.pixelSize: 21
                                        font.bold: true
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: searchClient.currentEmail || "未绑定邮箱"
                                        color: "#657386"
                                        font.pixelSize: 13
                                        Layout.fillWidth: true
                                        elide: Text.ElideRight
                                    }
                                }
                                AppButton {
                                    text: "更换头像"
                                    compact: true
                                    subtle: true
                                    onClicked: avatarDialog.open()
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 1
                                color: "#e5ebf3"
                            }

                            ProfileRow { label: "系统 ID"; value: searchClient.currentUserId || "-" }
                            ProfileRow { label: "用户名"; value: searchClient.currentUser || "-" }
                            ProfileRow { label: "邮箱"; value: searchClient.currentEmail || "-" }
                            ProfileRow { label: "角色"; value: roleLabel(searchClient.currentRole) }
                            ProfileRow { label: "状态"; value: searchClient.isLoggedIn ? "正常" : "未登录" }
                            ProfileRow { label: "创建时间"; value: searchClient.currentCreatedAt || "-" }
                            Item { Layout.fillHeight: true }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 12

                            Text {
                                text: "修改密码"
                                color: "#172033"
                                font.pixelSize: 18
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: "修改后会退出当前登录，请使用新密码重新登录。"
                                color: "#657386"
                                font.pixelSize: 13
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                            InputField {
                                id: currentPasswordField
                                placeholderText: "当前密码"
                                echoMode: TextInput.Password
                                Layout.fillWidth: true
                            }
                            InputField {
                                id: newPasswordField
                                placeholderText: "新密码（至少 6 位）"
                                echoMode: TextInput.Password
                                Layout.fillWidth: true
                            }
                            InputField {
                                id: confirmPasswordField
                                placeholderText: "再次输入新密码"
                                echoMode: TextInput.Password
                                Layout.fillWidth: true
                            }
                            AppButton {
                                text: "保存新密码"
                                highlighted: true
                                Layout.alignment: Qt.AlignLeft
                                onClicked: {
                                    if (newPasswordField.text !== confirmPasswordField.text) {
                                        messageDialog.message = "两次输入的新密码不一致"
                                        messageDialog.open()
                                        return
                                    }
                                    searchClient.changePassword(currentPasswordField.text, newPasswordField.text)
                                    currentPasswordField.clear()
                                    newPasswordField.clear()
                                    confirmPasswordField.clear()
                                }
                            }
                            Item { Layout.fillHeight: true }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 22
                            spacing: 14

                            Text {
                                text: "注销用户"
                                color: "#c43e3e"
                                font.pixelSize: 18
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: "注销后当前账号会被停用，已有登录状态会失效。项目和审计记录会保留，便于系统追溯。"
                                color: "#5e6b7d"
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            AppButton {
                                text: "注销当前账号"
                                danger: true
                                Layout.alignment: Qt.AlignLeft
                                onClicked: openConfirm("deactivateAccount", 0, "确认注销账号", "确定要注销当前账号吗？此操作会立即退出登录，并停用该账号。")
                            }
                            Item { Layout.fillHeight: true }
                        }
                    }
                }
            }
        }
    }

    AppDialog {
        id: adminCreateUserDialog
        property string selectedRole: "operator"
        title: "新建用户"
        anchors.centerIn: parent
        width: 440
        modal: true
        padding: 18
        onOpened: {
            selectedRole = "operator"
            adminCreateUsernameField.clear()
            adminCreateEmailField.clear()
            adminCreatePasswordField.clear()
            adminCreateRoleCombo.currentIndex = 1
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            InputField {
                id: adminCreateUsernameField
                placeholderText: "用户名"
                Layout.fillWidth: true
            }
            InputField {
                id: adminCreateEmailField
                placeholderText: "邮箱（可选）"
                Layout.fillWidth: true
            }
            InputField {
                id: adminCreatePasswordField
                placeholderText: "初始密码（至少 6 位）"
                echoMode: TextInput.Password
                Layout.fillWidth: true
            }
            ComboBox {
                id: adminCreateRoleCombo
                Layout.fillWidth: true
                model: ["admin", "operator", "viewer"]
                currentIndex: 1
                onActivated: adminCreateUserDialog.selectedRole = currentText
                background: Rectangle {
                    radius: 7
                    color: "#ffffff"
                    border.color: adminCreateRoleCombo.activeFocus ? "#2f6fed" : "#d8dee9"
                }
                contentItem: Text {
                    text: roleLabel(adminCreateRoleCombo.currentText)
                    color: "#172033"
                    font.pixelSize: 13
                    verticalAlignment: Text.AlignVCenter
                    leftPadding: 12
                    elide: Text.ElideRight
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                AppButton {
                    text: "取消"
                    Layout.fillWidth: true
                    subtle: true
                    onClicked: adminCreateUserDialog.close()
                }
                AppButton {
                    text: "创建用户"
                    highlighted: true
                    Layout.fillWidth: true
                    onClicked: searchClient.createAdminUser(
                        adminCreateUsernameField.text,
                        adminCreateEmailField.text,
                        adminCreatePasswordField.text,
                        adminCreateUserDialog.selectedRole
                    )
                }
            }
        }
    }

    AppDialog {
        id: adminResetPasswordDialog
        property string targetUsername: ""
        title: "重置密码"
        anchors.centerIn: parent
        width: 420
        modal: true
        padding: 18
        onOpened: {
            adminResetPasswordField.clear()
            adminResetPasswordConfirmField.clear()
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Text {
                text: "用户 " + adminResetPasswordDialog.targetUsername
                color: "#172033"
                font.pixelSize: 15
                font.bold: true
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            InputField {
                id: adminResetPasswordField
                placeholderText: "新密码（至少 6 位）"
                echoMode: TextInput.Password
                Layout.fillWidth: true
            }
            InputField {
                id: adminResetPasswordConfirmField
                placeholderText: "再次输入新密码"
                echoMode: TextInput.Password
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                AppButton {
                    text: "取消"
                    Layout.fillWidth: true
                    subtle: true
                    onClicked: adminResetPasswordDialog.close()
                }
                AppButton {
                    text: "确认重置"
                    highlighted: true
                    Layout.fillWidth: true
                    onClicked: {
                        if (adminResetPasswordField.text !== adminResetPasswordConfirmField.text) {
                            messageDialog.message = "两次输入的新密码不一致"
                            messageDialog.open()
                            return
                        }
                        searchClient.resetAdminUserPassword(adminResetPasswordDialog.targetUsername, adminResetPasswordField.text)
                    }
                }
            }
        }
    }

    AppDialog {
        id: projectDialog
        property string projectMode: "media"
        title: "新建项目"
        anchors.centerIn: parent
        width: 440
        modal: true
        padding: 18
        onOpened: {
            projectMode = "media"
            if (searchClient.isLoggedIn)
                searchClient.fetchCameras()
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                SegmentButton {
                    text: "媒体项目"
                    selected: projectDialog.projectMode === "media"
                    onClicked: projectDialog.projectMode = "media"
                }
                SegmentButton {
                    text: "实时相机"
                    selected: projectDialog.projectMode === "camera"
                    onClicked: projectDialog.projectMode = "camera"
                }
            }
            InputField {
                id: projectNameField
                placeholderText: "项目名称"
                Layout.fillWidth: true
            }
            InputArea {
                id: projectDescriptionField
                placeholderText: "项目描述"
                Layout.fillWidth: true
                Layout.preferredHeight: 96
                wrapMode: TextArea.Wrap
            }
            InputField {
                id: projectCameraSourceField
                visible: projectDialog.projectMode === "camera"
                placeholderText: "摄像头编号或视频流地址，默认 0"
                text: "0"
                Layout.fillWidth: true
            }
            RowLayout {
                visible: projectDialog.projectMode === "camera"
                Layout.fillWidth: true
                spacing: 8
                ComboBox {
                    id: projectCameraCombo
                    Layout.fillWidth: true
                    model: searchClient.availableCameraList
                    textRole: "label"
                    valueRole: "source"
                    enabled: count > 0
                    onActivated: projectCameraSourceField.text = String(currentValue)
                    background: Rectangle {
                        radius: 7
                        color: projectCameraCombo.enabled ? "#ffffff" : "#f3f6fa"
                        border.color: "#d8dee9"
                    }
                    contentItem: Text {
                        text: projectCameraCombo.count > 0 ? projectCameraCombo.displayText : "未检测到可用摄像头"
                        color: projectCameraCombo.count > 0 ? "#172033" : "#8a96a8"
                        font.pixelSize: 13
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        leftPadding: 12
                    }
                }
                AppButton {
                    text: "刷新设备"
                    compact: true
                    subtle: true
                    onClicked: searchClient.fetchCameras()
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                AppButton {
                    text: "取消"
                    Layout.fillWidth: true
                    subtle: true
                    onClicked: projectDialog.close()
                }
                AppButton {
                    text: projectDialog.projectMode === "camera" ? "创建并启用入库" : "创建并进入"
                    highlighted: true
                    Layout.fillWidth: true
                    onClicked: {
                        if (projectDialog.projectMode === "camera") {
                            searchClient.createCameraProject(projectNameField.text, projectDescriptionField.text, projectCameraSourceField.text)
                            mainWindow.currentPage = "camera"
                        } else {
                            searchClient.createProject(projectNameField.text, projectDescriptionField.text)
                            mainWindow.currentPage = "media"
                        }
                        projectNameField.clear()
                        projectDescriptionField.clear()
                        projectCameraSourceField.text = "0"
                        projectDialog.close()
                    }
                }
            }
        }
    }

    FileDialog {
        id: galleryDialog
        title: "选择图库图片"
        nameFilters: ["Image files (*.jpg *.jpeg *.png *.webp *.bmp)"]
        fileMode: FileDialog.OpenFiles
        onAccepted: searchClient.uploadGallery(selectedFiles, mainWindow.isAppendMode)
    }

    FileDialog {
        id: videoDialog
        title: "选择监控视频"
        nameFilters: ["Video files (*.mp4 *.avi *.mov *.mkv)"]
        onAccepted: searchClient.uploadVideo(selectedFile, mainWindow.isAppendMode)
    }

    FileDialog {
        id: queryDialog
        title: "选择查询图片"
        nameFilters: ["Image files (*.jpg *.jpeg *.png *.webp *.bmp)"]
        onAccepted: submitImageSearch(selectedFile)
    }

    FileDialog {
        id: avatarDialog
        title: "选择头像图片"
        nameFilters: ["Image files (*.jpg *.jpeg *.png *.webp)"]
        onAccepted: searchClient.uploadAvatar(selectedFile)
    }

    FileDialog {
        id: downloadDialog
        title: "保存文件"
        fileMode: FileDialog.SaveFile
        nameFilters: ["所有文件 (*)"]
        onAccepted: {
            searchClient.downloadFile(pendingDownloadSource, pendingDownloadName, selectedFile)
            pendingDownloadSource = ""
            pendingDownloadName = ""
        }
        onRejected: {
            pendingDownloadSource = ""
            pendingDownloadName = ""
        }
    }

    Popup {
        id: userMenu
        x: mainWindow.width - width - 24
        y: 76
        width: 210
        height: userMenuContent.implicitHeight + 18
        modal: false
        focus: true
        padding: 9
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle {
            color: "#ffffff"
            radius: 8
            border.color: "#d8dee9"
        }

        contentItem: ColumnLayout {
            id: userMenuContent
            spacing: 8
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Rectangle {
                    Layout.preferredWidth: 34
                    Layout.preferredHeight: 34
                    radius: 17
                    color: "#2f6fed"
                    clip: true
                    Image {
                        anchors.fill: parent
                        source: searchClient.currentAvatarUrl
                        fillMode: Image.PreserveAspectCrop
                        visible: searchClient.currentAvatarUrl.length > 0
                    }
                    Text {
                        anchors.centerIn: parent
                        text: accountInitial()
                        color: "white"
                        font.pixelSize: 14
                        font.bold: true
                        visible: searchClient.currentAvatarUrl.length === 0
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: searchClient.currentUser || "-"
                        color: "#172033"
                        font.pixelSize: 13
                        font.bold: true
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    Text {
                        text: searchClient.currentEmail || roleLabel(searchClient.currentRole)
                        color: "#657386"
                        font.pixelSize: 11
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: "#e7edf5"
            }
            MenuAction {
                text: "用户信息"
                onClicked: {
                    userMenu.close()
                    userDialog.open()
                }
            }
            MenuAction {
                visible: searchClient.currentRole === "admin"
                text: "用户管理"
                onClicked: {
                    userMenu.close()
                    currentPage = "users"
                    searchClient.fetchAdminUsers()
                }
            }
            MenuAction {
                text: "退出登录"
                danger: true
                onClicked: {
                    userMenu.close()
                    searchClient.logout()
                }
            }
        }
    }

    StackLayout {
        anchors.fill: parent
        currentIndex: appMode === "launcher" ? 0 : (appMode === "reid" ? 1 : 2)

        Launcher {
            app: mainWindow
        }

        RowLayout {
            spacing: 0

            AppSidebar {
                Layout.fillHeight: true
                app: mainWindow
                client: searchClient
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                AppHeader {
                    id: headerBar
                    Layout.fillWidth: true
                    app: mainWindow
                    client: searchClient
                }

                AppContent {
                    id: contentArea
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    app: mainWindow
                    client: searchClient
                    resultModel: resultModel
                }
            }
        }

        ScenicApp {
            app: mainWindow
            client: scenicClient
        }
    }

}
