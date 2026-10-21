pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia
import QtQuick.Dialogs
import "."

Rectangle {
    id: scenicAppRoot
    required property var app
    required property var client

    color: "#eef2f6"

    property string activeTab: "registration"
    property string localEntryImageUrl: ""
    property bool scenicRegisterMode: false
    property bool registrationInFlight: false
    property var touristRows: []
    property int pendingSimulatePointId: 0
    property int libraryCurrent: 0
    property int libraryTotal: 0
    property real libraryProgress: 0
    property string libraryStatusText: "等待入库"
    property string libraryDetailText: "尚未检测到可检索目标"
    property int monitorInfoTab: 0
    property int segmentModelSessionId: 0
    property int segmentSelectionAnchorIndex: -1
    property bool segmentSelectionAnchorIncluded: true

    function showMessage(message) {
        if (!message || message.length === 0) {
            return
        }
        app.messageDialog.message = message
        app.messageDialog.open()
    }

    function hasSelectedSession() {
        return client.selectedTouristSession && Number(client.selectedTouristSession.id || 0) > 0
    }

    function selectedSessionId() {
        return hasSelectedSession() ? Number(client.selectedTouristSession.id) : 0
    }

    function statusLabel(status) {
        if (status === "matched") return "已匹配"
        if (status === "no_match") return "无匹配"
        if (status === "rendering") return "成片中"
        if (status === "vlog_ready") return "可交付"
        if (status === "paid") return "已交付"
        return "已登记"
    }

    function statusColor(status) {
        if (status === "matched" || status === "vlog_ready" || status === "paid") return "#0f9f6e"
        if (status === "no_match") return "#dc2626"
        if (status === "rendering") return "#b7791f"
        return "#2563eb"
    }

    function money(cents) {
        return "¥" + (Number(cents || 0) / 100).toFixed(0)
    }

    function jobLabel(status) {
        if (status === "queued") return "排队"
        if (status === "running") return "处理中"
        if (status === "succeeded") return "完成"
        if (status === "failed") return "失败"
        return status || "未知"
    }

    function mediaCountByStatus(statuses) {
        var total = 0
        var list = client.mediaList || []
        for (var i = 0; i < list.length; i++) {
            if (statuses.indexOf(String(list[i].status || "")) >= 0) {
                total += 1
            }
        }
        return total
    }

    function targetCount() {
        var total = 0
        var list = client.mediaList || []
        for (var i = 0; i < list.length; i++) {
            total += Number(list[i].target_count || 0)
        }
        return total
    }

    function latestIndexJob() {
        var list = client.jobList || []
        for (var i = 0; i < list.length; i++) {
            if ((list[i].type || "") === "media_index") {
                return list[i]
            }
        }
        return null
    }

    function segmentDurationLabel(startSeconds, endSeconds) {
        var start = Number(startSeconds || 0)
        var end = Number(endSeconds || 0)
        return start.toFixed(1) + "s - " + end.toFixed(1) + "s"
    }

    function normalizeSegment(segment) {
        return {
            "segment_id": Number(segment.id || 0),
            "target_id": Number(segment.target_id || 0),
            "media_id": Number(segment.media_id || 0),
            "camera_id": String(segment.camera_id || ""),
            "camera_name": String(segment.camera_name || "未知点位"),
            "timestamp": String(segment.timestamp || ""),
            "start_seconds": Number(segment.start_seconds || 0),
            "end_seconds": Number(segment.end_seconds || 0),
            "duration_seconds": Number(segment.duration_seconds || 0),
            "similarity": Number(segment.similarity || 0),
            "thumbnail_url": String(segment.thumbnail_url || ""),
            "original_media_url": String(segment.original_media_url || ""),
            "included": segment.included !== false,
            "sort_order": Number(segment.sort_order || 0),
            "created_at": String(segment.created_at || "")
        }
    }

    function normalizeVlog(vlog) {
        return {
            "vlog_id": Number(vlog.id || 0),
            "processing_job_id": String(vlog.processing_job_id || ""),
            "status": String(vlog.status || ""),
            "preview_url": String(vlog.preview_url || ""),
            "full_url": String(vlog.full_url || ""),
            "preview_filename": String(vlog.preview_filename || ""),
            "full_filename": String(vlog.full_filename || ""),
            "segment_count": Number(vlog.segment_count || 0),
            "duration_seconds": Number(vlog.duration_seconds || 0),
            "error": String(vlog.error || ""),
            "created_at": String(vlog.created_at || ""),
            "updated_at": String(vlog.updated_at || "")
        }
    }

    function normalizeOrder(order) {
        return {
            "order_id": Number(order.id || 0),
            "order_no": String(order.order_no || ""),
            "status": String(order.status || ""),
            "amount_cents": Number(order.amount_cents || 0),
            "payment_provider": String(order.payment_provider || ""),
            "provider_trade_state": String(order.provider_trade_state || ""),
            "provider_trade_no": String(order.provider_trade_no || ""),
            "payment_qr_url": String(order.payment_qr_url || ""),
            "payment_error": String(order.payment_error || ""),
            "payment_code": String(order.payment_code || ""),
            "delivery_url": String(order.delivery_url || ""),
            "preview_url": String(order.preview_url || ""),
            "full_url": String(order.full_url || ""),
            "created_at": String(order.created_at || ""),
            "paid_at": String(order.paid_at || "")
        }
    }

    function includedSegmentCount() {
        var total = 0
        for (var i = 0; i < scenicSegmentModel.count; i++) {
            if (scenicSegmentModel.get(i).included !== false) {
                total += 1
            }
        }
        return total
    }

    function refreshSelectedSessionModels() {
        scenicSegmentModel.clear()
        scenicVlogModel.clear()
        scenicOrderModel.clear()
        var session = client.selectedTouristSession
        if (!session) {
            segmentModelSessionId = 0
            segmentSelectionAnchorIndex = -1
            return
        }
        var sessionId = Number(session.id || 0)
        if (sessionId !== segmentModelSessionId) {
            segmentModelSessionId = sessionId
            segmentSelectionAnchorIndex = -1
        }
        var segs = session.segments || []
        for (var i = 0; i < segs.length; i++) {
            scenicSegmentModel.append(normalizeSegment(segs[i]))
        }
        var vlogs = session.vlog_jobs || []
        for (var j = 0; j < vlogs.length; j++) {
            scenicVlogModel.append(normalizeVlog(vlogs[j]))
        }
        var orders = session.orders || []
        for (var k = 0; k < orders.length; k++) {
            scenicOrderModel.append(normalizeOrder(orders[k]))
        }
    }

    function applySegmentIndexes(indexes, included) {
        var ids = []
        var seen = ({})
        var firstSortOrder = 0
        for (var i = 0; i < indexes.length; i++) {
            var rowIndex = Number(indexes[i])
            if (rowIndex < 0 || rowIndex >= scenicSegmentModel.count) {
                continue
            }
            var segment = scenicSegmentModel.get(rowIndex)
            var segmentId = Number(segment.segment_id || 0)
            if (segmentId <= 0 || seen[segmentId]) {
                continue
            }
            seen[segmentId] = true
            scenicSegmentModel.setProperty(rowIndex, "included", included)
            if (firstSortOrder === 0) {
                firstSortOrder = Number(segment.sort_order || 0)
            }
            ids.push(segmentId)
        }
        if (ids.length === 1) {
            client.updateTouristSegment(selectedSessionId(), ids[0], included, firstSortOrder)
        } else if (ids.length > 1) {
            client.updateTouristSegments(selectedSessionId(), ids, included)
        }
    }

    function setAllSegmentsIncluded(included) {
        var indexes = []
        for (var i = 0; i < scenicSegmentModel.count; i++) {
            indexes.push(i)
        }
        segmentSelectionAnchorIndex = indexes.length > 0 ? indexes[0] : -1
        segmentSelectionAnchorIncluded = included
        applySegmentIndexes(indexes, included)
    }

    function invertSegmentSelection() {
        var enableIds = []
        var disableIds = []
        for (var i = 0; i < scenicSegmentModel.count; i++) {
            var segment = scenicSegmentModel.get(i)
            var nextIncluded = segment.included === false
            scenicSegmentModel.setProperty(i, "included", nextIncluded)
            if (nextIncluded) {
                enableIds.push(Number(segment.segment_id || 0))
            } else {
                disableIds.push(Number(segment.segment_id || 0))
            }
        }
        segmentSelectionAnchorIndex = -1
        if (enableIds.length > 0) {
            client.updateTouristSegments(selectedSessionId(), enableIds, true)
        }
        if (disableIds.length > 0) {
            client.updateTouristSegments(selectedSessionId(), disableIds, false)
        }
    }

    function setSegmentSelectionAt(index, included, modifiers) {
        var rowIndex = Number(index)
        if (rowIndex < 0 || rowIndex >= scenicSegmentModel.count) {
            return
        }
        var useRange = (Number(modifiers || 0) & Qt.ShiftModifier) && segmentSelectionAnchorIndex >= 0
        if (useRange) {
            var first = Math.min(segmentSelectionAnchorIndex, rowIndex)
            var last = Math.max(segmentSelectionAnchorIndex, rowIndex)
            var rangeIndexes = []
            for (var i = first; i <= last; i++) {
                rangeIndexes.push(i)
            }
            applySegmentIndexes(rangeIndexes, segmentSelectionAnchorIncluded)
            return
        }
        segmentSelectionAnchorIndex = rowIndex
        segmentSelectionAnchorIncluded = included
        applySegmentIndexes([rowIndex], included)
    }

    function updateSegmentIncluded(segmentId, included, sortOrder, index, modifiers) {
        if (index !== undefined && Number(index) >= 0) {
            setSegmentSelectionAt(Number(index), included, modifiers || 0)
            return
        }
        var id = Number(segmentId || 0)
        for (var i = 0; i < scenicSegmentModel.count; i++) {
            if (Number(scenicSegmentModel.get(i).segment_id || 0) === id) {
                scenicSegmentModel.setProperty(i, "included", included)
                break
            }
        }
        client.updateTouristSegment(selectedSessionId(), id, included, Number(sortOrder || 0))
    }

    function refreshLibraryStatus() {
        var latest = latestIndexJob()
        var targets = targetCount()
        var processing = mediaCountByStatus(["uploaded", "processing", "streaming"])
        var failed = mediaCountByStatus(["failed"])
        var indexed = mediaCountByStatus(["indexed"])
        if (latest && (latest.status === "queued" || latest.status === "running")) {
            libraryStatusText = latest.status === "queued" ? "等待目标检测" : "目标检测与特征提取中"
            libraryDetailText = latest.message || "正在复用基础系统模型处理视频"
            libraryProgress = Math.max(0, Math.min(1, Number(latest.progress || 0)))
        } else if (failed > 0) {
            libraryStatusText = "入库异常"
            libraryDetailText = latest && latest.error ? latest.error : "存在处理失败的视频"
            libraryProgress = 1
        } else if (targets > 0) {
            libraryStatusText = "可匹配"
            libraryDetailText = "已入库 " + String(targets) + " 个目标，媒体 " + String(indexed) + " 个"
            libraryProgress = 1
        } else if (processing > 0) {
            libraryStatusText = "等待处理"
            libraryDetailText = "视频已上传，等待目标检测启动"
            libraryProgress = 0.05
        } else {
            libraryStatusText = "等待入库"
            libraryDetailText = "上传模拟视频后会自动检测行人并提取特征"
            libraryProgress = 0
        }
    }

    function pageIndex() {
        if (activeTab === "registration") return 0
        if (activeTab === "monitor") return 1
        if (activeTab === "matching") return 2
        if (activeTab === "vlog") return 3
        if (activeTab === "history") return 4
        if (activeTab === "audit") return 5
        return 0
    }

    function pageTitle() {
        if (activeTab === "registration") return "游客登记"
        if (activeTab === "monitor") return "园内摄像头"
        if (activeTab === "matching") return "游客游玩记录匹配"
        if (activeTab === "vlog") return "生成视频短片"
        if (activeTab === "history") return "游客历史"
        if (activeTab === "audit") return "审计"
        return "景区后台"
    }

    function clearRegistrationForm() {
        localEntryImageUrl = ""
        regTicketField.text = ""
        regLabelField.text = ""
        regContactField.text = ""
        visitorTypeCombo.currentIndex = 0
        attentionCheck.checked = false
        collectContactCheck.checked = true
        consentCheck.checked = true
    }

    function submitRegistration() {
        if (localEntryImageUrl.length === 0) {
            showMessage("请先上传照片或使用实时相机抓拍")
            return
        }
        if (!consentCheck.checked) {
            showMessage("需要确认游客授权后才能登记")
            return
        }
        var label = regLabelField.text.trim()
        if (visitorTypeCombo.currentIndex > 0) {
            label = label.length > 0 ? (label + " · " + visitorTypeCombo.currentText) : visitorTypeCombo.currentText
        }
        if (attentionCheck.checked) {
            label = label.length > 0 ? (label + " · 重点照看") : "重点照看"
        }
        registrationInFlight = true
        client.registerTourist(
            localEntryImageUrl,
            regTicketField.text,
            label,
            collectContactCheck.checked ? regContactField.text : ""
        )
    }

    ListModel { id: scenicTouristModel }
    ListModel { id: scenicSegmentModel }
    ListModel { id: scenicVlogModel }
    ListModel { id: scenicOrderModel }
    ListModel { id: scenicCameraWallModel }
    ListModel { id: scenicHistoryModel }
    ListModel { id: scenicAuditModel }
    ListModel { id: scenicJobModel }

    Connections {
        target: client

        function onTouristSessionListChanged() {
            touristRows = client.touristSessionList || []
            scenicTouristModel.clear()
            var list = client.touristSessionList || []
            for (var i = 0; i < list.length; i++) {
                scenicTouristModel.append(list[i])
            }
        }

        function onSelectedTouristSessionChanged() {
            scenicAppRoot.refreshSelectedSessionModels()
        }

        function onScenicCameraWallChanged() {
            scenicCameraWallModel.clear()
            var wall = client.scenicCameraWall || []
            for (var i = 0; i < wall.length; i++) {
                scenicCameraWallModel.append(wall[i])
            }
            scenicAppRoot.refreshLibraryStatus()
        }

        function onJobListChanged() {
            scenicJobModel.clear()
            var list = client.jobList || []
            for (var i = 0; i < list.length; i++) {
                scenicJobModel.append(list[i])
            }
            scenicAppRoot.refreshLibraryStatus()
        }

        function onMediaListChanged() {
            scenicAppRoot.refreshLibraryStatus()
        }

        function onProgressUpdated(current, total) {
            libraryCurrent = current
            libraryTotal = total
            scenicAppRoot.refreshLibraryStatus()
        }

        function onSystemStatusChanged(isReady) {
            if (isReady) {
                client.fetchMedia()
                client.fetchJobs()
                client.fetchScenicCameraWall()
                if (activeTab === "vlog" && scenicAppRoot.hasSelectedSession()) {
                    client.selectTouristSession(scenicAppRoot.selectedSessionId())
                }
            }
        }

        function onHistoryListChanged() {
            scenicHistoryModel.clear()
            var list = client.historyList || []
            for (var i = 0; i < list.length; i++) {
                scenicHistoryModel.append(list[i])
            }
        }

        function onAuditListChanged() {
            scenicAuditModel.clear()
            var list = client.auditList || []
            for (var i = 0; i < list.length; i++) {
                scenicAuditModel.append(list[i])
            }
        }

        function onAuthFinished(success, message) {
            showMessage(message)
            if (success) {
                client.refreshWorkspace()
            }
        }

        function onEmailCodeRequested(success, message) {
            showMessage(message)
        }

        function onScenicOperationFinished(success, message) {
            showMessage(message)
            if (success && registrationInFlight && message.indexOf("入园游客已登记") >= 0) {
                registrationInFlight = false
                clearRegistrationForm()
            } else if (!success) {
                registrationInFlight = false
            }
        }

        function onErrorOccurred(errorMsg) {
            showMessage(errorMsg)
        }

        function onFileDownloaded(success, message) {
            showMessage(message)
        }

        function onScenicCameraSnapshotReady(url, filename) {
            localEntryImageUrl = url
            activeTab = "registration"
        }
    }

    Component.onCompleted: {
        if (client.isLoggedIn) {
            client.refreshWorkspace()
        }
    }

    Timer {
        interval: 3000
        repeat: true
        running: client.isLoggedIn
        onTriggered: {
            client.fetchScenicCameraWall()
            client.fetchScenicDashboard()
            client.fetchJobs()
            client.fetchMedia()
            if (activeTab === "history") {
                client.fetchHistory()
                client.fetchTourists()
            }
            if (activeTab === "audit") {
                client.fetchAudit()
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: client.isLoggedIn
        color: "#eef2f6"

        RowLayout {
            anchors.fill: parent
            spacing: 0

            Rectangle {
                Layout.preferredWidth: 248
                Layout.fillHeight: true
                color: "#0F172A"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 14

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12

                        Rectangle {
                            Layout.preferredWidth: 40
                            Layout.preferredHeight: 40
                            radius: 10
                            color: "#4F46E5"
                            Text {
                                anchors.centerIn: parent
                                text: "景"
                                color: "#ffffff"
                                font.pixelSize: 20
                                font.bold: true
                            }
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 1
                            Text {
                                text: "景区运营平台"
                                color: "#ffffff"
                                font.pixelSize: 18
                                font.bold: true
                                Layout.fillWidth: true
                            }
                            Text {
                                text: "独立后台管理"
                                color: "#93a4bb"
                                font.pixelSize: 12
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 82
                        radius: 8
                        color: "#172235"
                        border.color: "#25324a"

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 6
                            Text {
                                text: client.currentProjectId > 0 ? client.currentProjectName : "默认景区项目"
                                color: "#f8fafc"
                                font.pixelSize: 14
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: client.isLoggedIn ? "当前账号 " + client.currentUser : "登录后启用景区后台"
                                color: "#93a4bb"
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            StatusPill {
                                text: client.cameraMonitorOnline ? "监控在线" : "等待相机"
                                toneColor: client.cameraMonitorOnline ? "#16a163" : "#d38b17"
                            }
                        }
                    }

                    Text {
                        text: "导航"
                        color: "#667891"
                        font.pixelSize: 11
                        font.bold: true
                        Layout.topMargin: 4
                        Layout.leftMargin: 4
                    }

                    Repeater {
                        model: [
                            {"key": "registration", "label": "游客登记", "icon": "登"},
                            {"key": "monitor", "label": "园内摄像头", "icon": "监"},
                            {"key": "matching", "label": "游客游玩记录匹配", "icon": "匹"},
                            {"key": "vlog", "label": "生成视频短片", "icon": "片"},
                            {"key": "history", "label": "游客历史", "icon": "历"},
                            {"key": "audit", "label": "审计", "icon": "审"}
                        ]

                        delegate: SidebarItem {
                            required property var modelData
                            Layout.fillWidth: true
                            label: modelData.label
                            iconText: modelData.icon
                            selected: scenicAppRoot.activeTab === modelData.key
                            onActivated: {
                                scenicAppRoot.activeTab = modelData.key
                                if (modelData.key === "history") {
                                    client.fetchHistory()
                                    client.fetchTourists()
                                } else if (modelData.key === "audit") {
                                    client.fetchAudit()
            } else if (modelData.key === "monitor") {
                client.fetchScenicCameraWall()
                client.fetchJobs()
                client.fetchMedia()
            } else if (modelData.key === "vlog" && scenicAppRoot.hasSelectedSession()) {
                client.selectTouristSession(scenicAppRoot.selectedSessionId())
            }
        }
    }
                    }

                    Item { Layout.fillHeight: true }

                    AppButton {
                        text: "返回系统选择"
                        Layout.fillWidth: true
                        subtle: true
                        onClicked: app.appMode = "launcher"
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 156
                        radius: 8
                        color: "#0b1220"
                        border.color: "#223047"

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: scenicAppRoot.libraryStatusText
                                    color: "#f8fafc"
                                    font.pixelSize: 13
                                    font.bold: true
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: String(Math.round(scenicAppRoot.libraryProgress * 100)) + "%"
                                    color: "#9dd7b6"
                                    font.pixelSize: 12
                                    font.bold: true
                                }
                            }
                            AppProgressBar {
                                Layout.fillWidth: true
                                value: scenicAppRoot.libraryProgress
                            }
                            Text {
                                text: scenicAppRoot.libraryDetailText
                                color: "#94a3b8"
                                font.pixelSize: 11
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                                maximumLineCount: 2
                            }
                            Text {
                                text: "点位 " + String(scenicCameraWallModel.count)
                                      + " · 媒体 " + String((client.mediaList || []).length)
                                      + " · 目标 " + String(scenicAppRoot.targetCount())
                                color: "#b9c6d8"
                                font.pixelSize: 11
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Text {
                                text: "监控: " + (client.cameraMonitorOnline ? "在线" : "等待") + " · " + (client.cameraMessage || "")
                                color: "#94a3b8"
                                font.pixelSize: 10
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 76
                    color: "#ffffff"
                    border.color: "#dbe4ee"

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 24
                        anchors.rightMargin: 24
                        spacing: 18

                        ColumnLayout {
                            spacing: 2
                            Layout.fillWidth: true
                            Text {
                                text: scenicAppRoot.pageTitle()
                                color: "#0f172a"
                                font.pixelSize: 22
                                font.bold: true
                            }
                            Text {
                                text: "今日 " + String(client.scenicDashboard.today_sessions || 0)
                                      + " · 匹配 " + String(client.scenicDashboard.matched_sessions || 0)
                                      + " · 短片 " + String(client.scenicDashboard.ready_vlogs || 0)
                                      + " · 收入 " + scenicAppRoot.money(client.scenicDashboard.paid_revenue_cents)
                                color: "#5f6f7f"
                                font.pixelSize: 12
                            }
                        }

                        AppButton {
                            text: "刷新"
                            subtle: true
                            onClicked: client.refreshWorkspace()
                        }
                    }
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: scenicAppRoot.pageIndex()

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ScrollView {
                            id: registrationScroll
                            anchors.fill: parent
                            anchors.margins: 20
                            contentWidth: availableWidth

                            ColumnLayout {
                                width: registrationScroll.availableWidth
                                spacing: 16

                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 16

                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 330
                                        radius: 8
                                        color: "#ffffff"
                                        border.color: "#dbe4ee"

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 16
                                            spacing: 12
                                            Text { text: "上传照片"; color: "#0f172a"; font.pixelSize: 16; font.bold: true }

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.fillHeight: true
                                                radius: 8
                                                color: "#f7fafc"
                                                border.color: "#cbd5e1"
                                                clip: true

                                                Image {
                                                    anchors.fill: parent
                                                    anchors.margins: 8
                                                    source: localEntryImageUrl
                                                    fillMode: Image.PreserveAspectFit
                                                    visible: localEntryImageUrl.length > 0
                                                    asynchronous: true
                                                }
                                                Text {
                                                    anchors.centerIn: parent
                                                    visible: localEntryImageUrl.length === 0
                                                    text: "选择入园照片"
                                                    color: "#64748b"
                                                    font.pixelSize: 16
                                                }
                                                TapHandler { onTapped: localGalleryDialog.open() }
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true
                                                AppButton {
                                                    text: "选择照片"
                                                    highlighted: true
                                                    Layout.fillWidth: true
                                                    onClicked: localGalleryDialog.open()
                                                }
                                                AppButton {
                                                    text: "清除"
                                                    subtle: true
                                                    Layout.fillWidth: true
                                                    onClicked: localEntryImageUrl = ""
                                                }
                                            }
                                        }
                                    }

                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 330
                                        radius: 8
                                        color: "#ffffff"
                                        border.color: "#dbe4ee"

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 16
                                            spacing: 12
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Text {
                                                    text: "实时相机抓拍"
                                                    color: "#0f172a"
                                                    font.pixelSize: 16
                                                    font.bold: true
                                                    Layout.fillWidth: true
                                                }
                                                Text {
                                                    text: client.cameraMonitorOnline ? "在线" : "离线"
                                                    color: client.cameraMonitorOnline ? "#0f9f6e" : "#dc2626"
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                }
                                            }

                                            Rectangle {
                                                Layout.fillWidth: true
                                                Layout.fillHeight: true
                                                radius: 8
                                                color: "#101820"
                                                clip: true
                                                Image {
                                                    anchors.fill: parent
                                                    anchors.margins: 8
                                                    source: client.cameraFrameUrl.length > 0 ? app.absoluteUrl(client.cameraFrameUrl) + "?v=" + client.cameraFrameVersion : ""
                                                    fillMode: Image.PreserveAspectFit
                                                    asynchronous: true
                                                }
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: client.cameraMonitorOnline ? "等待实时画面" : "等待统一监控"
                                                    color: "#94a3b8"
                                                    font.pixelSize: 15
                                                    visible: client.cameraFrameUrl.length === 0
                                                }
                                            }

                                            RowLayout {
                                                Layout.fillWidth: true
                                                AppButton {
                                                    text: "抓拍照片"
                                                    highlighted: true
                                                    Layout.fillWidth: true
                                                    enabled: client.cameraMonitorOnline
                                                    onClicked: client.takeScenicCameraSnapshot()
                                                }
                                            }
                                        }
                                    }

                                    Rectangle {
                                        Layout.preferredWidth: 330
                                        Layout.fillHeight: true
                                        Layout.minimumHeight: 460
                                        radius: 8
                                        color: "#ffffff"
                                        border.color: "#dbe4ee"

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 16
                                            spacing: 10
                                            Text { text: "登记信息"; color: "#0f172a"; font.pixelSize: 16; font.bold: true }
                                            InputField { id: regTicketField; placeholderText: "门票号"; Layout.fillWidth: true }
                                            InputField { id: regLabelField; placeholderText: "游客备注"; Layout.fillWidth: true }
                                            InputField { id: regContactField; placeholderText: "联系方式"; Layout.fillWidth: true; enabled: collectContactCheck.checked }
                                            ComboBox {
                                                id: visitorTypeCombo
                                                Layout.fillWidth: true
                                                model: ["普通游客", "亲子家庭", "团体游客", "重点服务"]
                                            }
                                            CheckBox { id: collectContactCheck; text: "记录联系方式"; checked: true }
                                            CheckBox { id: attentionCheck; text: "重点照看"; checked: false }
                                            CheckBox { id: consentCheck; text: "已获得游客授权"; checked: true }
                                            Item { Layout.fillHeight: true }
                                            AppButton {
                                                text: "提交登记"
                                                highlighted: true
                                                Layout.fillWidth: true
                                                onClicked: scenicAppRoot.submitRegistration()
                                            }
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 280
                                    radius: 8
                                    color: "#ffffff"
                                    border.color: "#dbe4ee"

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 16
                                        spacing: 10
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text { text: "最近登记"; color: "#0f172a"; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true }
                                            AppButton { text: "刷新游客"; subtle: true; onClicked: client.fetchTourists() }
                                        }
                                        ListView {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            model: scenicAppRoot.touristRows
                                            clip: true
                                            spacing: 8
                                            delegate: TouristRow {
                                                required property var modelData
                                                width: ListView.view.width
                                                app: scenicAppRoot.app
                                                rowModel: modelData || ({})
                                                selected: scenicAppRoot.hasSelectedSession() && client.selectedTouristSession.id === modelData.id
                                                onPicked: function(sessionId) {
                                                    client.selectTouristSession(sessionId)
                                                    scenicAppRoot.activeTab = "matching"
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 20
                            spacing: 14

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                spacing: 14

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                AppButton { text: "同步本机相机"; subtle: true; onClicked: client.syncScenicCameraPoints() }
                                AppButton { text: "启动监控墙"; onClicked: client.startScenicMonitoring() }
                                AppButton { text: "停止监控墙"; subtle: true; onClicked: client.stopScenicMonitoring() }
                                Item { Layout.fillWidth: true }
                                Text {
                                    text: "在线 " + String(client.scenicDashboard.active_camera_points || scenicCameraWallModel.count) + " · 已入库目标 " + String(client.cameraTargetCount || 0)
                                    color: "#506070"
                                    font.pixelSize: 13
                                }
                            }

                            GridView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                model: scenicCameraWallModel
                                clip: true
                                cellWidth: Math.max(280, Math.floor(width / Math.max(1, Math.floor(width / 340))))
                                cellHeight: 250
                                delegate: Rectangle {
                                    width: GridView.view.cellWidth - 12
                                    height: 236
                                    radius: 8
                                    color: "#ffffff"
                                    border.color: "#dbe4ee"

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        spacing: 8

                                        Rectangle {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            radius: 6
                                            color: "#101820"
                                            clip: true
                                            Image {
                                                anchors.fill: parent
                                                anchors.margins: 6
                                                source: latest_frame_url ? app.absoluteUrl(latest_frame_url) + "?v=" + latest_frame_version : ""
                                                fillMode: Image.PreserveAspectFit
                                                asynchronous: true
                                            }
                                            Text {
                                                anchors.centerIn: parent
                                                visible: !latest_frame_url
                                                text: running ? "等待画面" : "未启动"
                                                color: "#94a3b8"
                                                font.pixelSize: 14
                                            }
                                        }

                                        RowLayout {
                                            Layout.fillWidth: true
                                            ColumnLayout {
                                                Layout.fillWidth: true
                                                spacing: 2
                                                Text { text: name || "摄像头"; color: "#0f172a"; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                                Text { text: (location || role || "园区点位") + " · " + (camera_source || "0"); color: "#64748b"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                                            }
                                            Rectangle {
                                                Layout.preferredWidth: 54
                                                Layout.preferredHeight: 24
                                                radius: 12
                                                color: running ? "#dcfce7" : "#fee2e2"
                                                Text {
                                                    anchors.centerIn: parent
                                                    text: running ? "采集" : "离线"
                                                    color: running ? "#0f8a5f" : "#b91c1c"
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                }
                                            }
                                        }

                                        Text {
                                            text: "目标 " + String(model.indexed_targets || 0) + " · " + (model.message || "")
                                            color: "#506070"
                                            font.pixelSize: 12
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }

                                Text {
                                    anchors.centerIn: parent
                                    visible: scenicCameraWallModel.count === 0
                                    text: "暂无摄像头点位"
                                    color: "#64748b"
                                    font.pixelSize: 18
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 188
                                radius: 8
                                color: "#ffffff"
                                border.color: "#dbe4ee"

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 8

                                    TabBar {
                                        Layout.fillWidth: true
                                        currentIndex: scenicAppRoot.monitorInfoTab
                                        onCurrentIndexChanged: scenicAppRoot.monitorInfoTab = currentIndex
                                        TabButton { text: "库状态" }
                                        TabButton { text: "处理任务" }
                                        TabButton { text: "媒体目标" }
                                    }

                                    StackLayout {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        currentIndex: scenicAppRoot.monitorInfoTab

                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            spacing: 8
                                            RowLayout {
                                                Layout.fillWidth: true
                                                MetricTile { title: "媒体"; value: String((client.mediaList || []).length); accentColor: "#4F46E5" }
                                                MetricTile { title: "目标"; value: String(scenicAppRoot.targetCount()); accentColor: "#0f9f6e" }
                                                MetricTile { title: "已入库"; value: String(scenicAppRoot.mediaCountByStatus(["indexed"])); accentColor: "#2563eb" }
                                            }
                                            Text {
                                                text: scenicAppRoot.libraryStatusText + " · " + scenicAppRoot.libraryDetailText
                                                color: "#506070"
                                                font.pixelSize: 12
                                                elide: Text.ElideRight
                                                Layout.fillWidth: true
                                            }
                                        }

                                        ListView {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            model: scenicJobModel
                                            clip: true
                                            spacing: 6
                                            delegate: Rectangle {
                                                required property var type
                                                required property var status
                                                required property var progress
                                                required property var message
                                                required property var error
                                                width: ListView.view.width
                                                height: 44
                                                radius: 6
                                                color: "#f8fafc"
                                                border.color: "#e2e8f0"
                                                RowLayout {
                                                    anchors.fill: parent
                                                    anchors.margins: 8
                                                    Text {
                                                        text: (type === "media_index" ? "目标检测/特征提取" : type) + " · " + scenicAppRoot.jobLabel(status)
                                                        color: status === "failed" ? "#dc2626" : "#0f172a"
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                        Layout.preferredWidth: 180
                                                        elide: Text.ElideRight
                                                    }
                                                    AppProgressBar { Layout.fillWidth: true; value: Number(progress || 0) }
                                                    Text { text: String(Math.round(Number(progress || 0) * 100)) + "%"; color: "#64748b"; font.pixelSize: 11 }
                                                }
                                            }
                                            Text {
                                                anchors.centerIn: parent
                                                visible: scenicJobModel.count === 0
                                                text: "暂无后台任务"
                                                color: "#64748b"
                                            }
                                        }

                                        ListView {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            model: client.mediaList || []
                                            clip: true
                                            spacing: 6
                                            delegate: Rectangle {
                                                required property var modelData
                                                width: ListView.view.width
                                                height: 44
                                                radius: 6
                                                color: "#f8fafc"
                                                border.color: "#e2e8f0"
                                                RowLayout {
                                                    anchors.fill: parent
                                                    anchors.margins: 8
                                                    Text {
                                                        text: modelData.filename || "媒体"
                                                        color: "#0f172a"
                                                        font.pixelSize: 12
                                                        font.bold: true
                                                        elide: Text.ElideRight
                                                        Layout.fillWidth: true
                                                    }
                                                    Text {
                                                        text: (modelData.status || "") + " · 目标 " + String(modelData.target_count || 0)
                                                        color: "#64748b"
                                                        font.pixelSize: 11
                                                    }
                                                }
                                            }
                                            Text {
                                                anchors.centerIn: parent
                                                visible: (client.mediaList || []).length === 0
                                                text: "暂无媒体入库"
                                                color: "#64748b"
                                            }
                                        }
                                    }
                                }
                            }
                        }

                        Rectangle {
                            Layout.preferredWidth: 320
                            Layout.fillHeight: true
                            radius: 8
                            color: "#ffffff"
                            border.color: "#dbe4ee"

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 16
                                spacing: 12
                                Text { text: "录制模拟器"; font.pixelSize: 16; font.bold: true; color: "#0f172a" }
                                Text { text: "上传后会自动执行行人目标检测和特征提取，完成后才能匹配出视频片段。"; font.pixelSize: 13; color: "#64748b"; wrapMode: Text.WordWrap; Layout.fillWidth: true }

                                ComboBox {
                                    id: simulateCameraCombo
                                    Layout.fillWidth: true
                                    model: scenicCameraWallModel
                                    textRole: "name"
                                    valueRole: "id"
                                }

                                AppButton {
                                    text: "选择视频并模拟"
                                    highlighted: true
                                    Layout.fillWidth: true
                                    onClicked: {
                                        if (simulateCameraCombo.currentValue) {
                                            scenicAppRoot.pendingSimulatePointId = simulateCameraCombo.currentValue
                                            simulateVideoDialog.open()
                                        } else {
                                            scenicAppRoot.showMessage("请先同步或选择一个目标摄像头")
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 86
                                    radius: 8
                                    color: "#f8fafc"
                                    border.color: "#dbe4ee"

                                    ColumnLayout {
                                        anchors.fill: parent
                                        anchors.margins: 10
                                        spacing: 6
                                        RowLayout {
                                            Layout.fillWidth: true
                                            Text { text: "入库状态"; color: "#0f172a"; font.pixelSize: 13; font.bold: true; Layout.fillWidth: true }
                                            Text { text: String(Math.round(scenicAppRoot.libraryProgress * 100)) + "%"; color: "#0f8a5f"; font.pixelSize: 12; font.bold: true }
                                        }
                                        AppProgressBar {
                                            Layout.fillWidth: true
                                            value: scenicAppRoot.libraryProgress
                                        }
                                        Text {
                                            text: scenicAppRoot.libraryDetailText
                                            color: "#64748b"
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                    }
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.fillHeight: true
                                    color: "#101820"
                                    radius: 6
                                    clip: true

                                    VideoOutput {
                                        id: simOutput
                                        anchors.fill: parent
                                        anchors.margins: 6
                                    }
                                    MediaPlayer {
                                        id: simPlayer
                                        videoOutput: simOutput
                                    }

                                    Text {
                                        anchors.centerIn: parent
                                        text: "视频预览"
                                        color: "#506070"
                                        font.pixelSize: 14
                                        visible: !simPlayer.source.toString()
                                    }
                                }

                                ListView {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 126
                                    model: scenicJobModel
                                    clip: true
                                    spacing: 6
                                    delegate: Rectangle {
                                        required property var type
                                        required property var status
                                        required property var progress
                                        required property var message
                                        required property var error
                                        width: ListView.view.width
                                        height: 58
                                        radius: 6
                                        color: "#f8fafc"
                                        border.color: "#e2e8f0"

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 8
                                            spacing: 4
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Text {
                                                    text: (type === "media_index" ? "目标检测/特征" : type) + " · " + scenicAppRoot.jobLabel(status)
                                                    color: status === "failed" ? "#dc2626" : "#0f172a"
                                                    font.pixelSize: 12
                                                    font.bold: true
                                                    elide: Text.ElideRight
                                                    Layout.fillWidth: true
                                                }
                                                Text {
                                                    text: String(Math.round(Number(progress || 0) * 100)) + "%"
                                                    color: "#64748b"
                                                    font.pixelSize: 11
                                                }
                                            }
                                            Text {
                                                text: error || message || ""
                                                color: error ? "#dc2626" : "#64748b"
                                                font.pixelSize: 10
                                                elide: Text.ElideRight
                                                Layout.fillWidth: true
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                    MatchingPage {
                        app: scenicAppRoot.app
                        client: scenicAppRoot.client
                        touristModel: scenicTouristModel
                        segmentModel: scenicSegmentModel
                        selected: scenicAppRoot.hasSelectedSession()
                        onGoVlog: scenicAppRoot.activeTab = "vlog"
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 20
                            spacing: 16

                            Rectangle {
                                Layout.preferredWidth: 320
                                Layout.fillHeight: true
                                radius: 8
                                color: "#ffffff"
                                border.color: "#dbe4ee"

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 14
                                    spacing: 10
                                    Text { text: "选择游客"; color: "#0f172a"; font.pixelSize: 16; font.bold: true }
                                    ListView {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        model: scenicAppRoot.touristRows
                                        clip: true
                                        spacing: 8
                                        delegate: TouristRow {
                                            required property var modelData
                                            width: ListView.view.width
                                            app: scenicAppRoot.app
                                            rowModel: modelData || ({})
                                            selected: scenicAppRoot.hasSelectedSession() && client.selectedTouristSession.id === modelData.id
                                            onPicked: function(sessionId) { client.selectTouristSession(sessionId) }
                                        }
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                radius: 8
                                color: "#ffffff"
                                border.color: "#dbe4ee"

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 16
                                    spacing: 14

                                    RowLayout {
                                        Layout.fillWidth: true
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            Text { text: scenicAppRoot.hasSelectedSession() ? (client.selectedTouristSession.visitor_label || client.selectedTouristSession.ticket_code) : "未选择游客"; color: "#0f172a"; font.pixelSize: 20; font.bold: true }
                                            Text { text: scenicAppRoot.hasSelectedSession() ? ("可用片段 " + String(client.selectedTouristSession.segment_count || scenicSegmentModel.count)) : "从左侧选择游客后生成短片"; color: "#64748b"; font.pixelSize: 13 }
                                        }
                                        AppButton {
                                            text: "生成视频短片"
                                            highlighted: true
                                            enabled: scenicAppRoot.hasSelectedSession() && scenicSegmentModel.count > 0
                                            onClicked: client.generateTouristVlog(scenicAppRoot.selectedSessionId())
                                        }
                                    }

                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 270
                                        radius: 8
                                        color: "#f8fafc"
                                        border.color: "#dbe4ee"

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 12
                                            spacing: 10
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Text { text: "用于成片的视频片段"; color: "#0f172a"; font.pixelSize: 15; font.bold: true; Layout.fillWidth: true }
                                                Text { text: "已选 " + String(scenicAppRoot.includedSegmentCount()) + " / " + String(scenicSegmentModel.count); color: "#64748b"; font.pixelSize: 12 }
                                                AppButton { text: "全选"; compact: true; subtle: true; enabled: scenicSegmentModel.count > 0; onClicked: scenicAppRoot.setAllSegmentsIncluded(true) }
                                                AppButton { text: "清空"; compact: true; subtle: true; enabled: scenicSegmentModel.count > 0; onClicked: scenicAppRoot.setAllSegmentsIncluded(false) }
                                                AppButton { text: "反选"; compact: true; subtle: true; enabled: scenicSegmentModel.count > 0; onClicked: scenicAppRoot.invertSegmentSelection() }
                                            }

                                            ListView {
                                                Layout.fillWidth: true
                                                Layout.fillHeight: true
                                                model: scenicSegmentModel
                                                orientation: ListView.Horizontal
                                                clip: true
                                                spacing: 10
                                                delegate: Rectangle {
                                                    required property int segment_id
                                                    required property string camera_name
                                                    required property string timestamp
                                                    required property real similarity
                                                    required property string thumbnail_url
                                                    required property string original_media_url
                                                    required property real start_seconds
                                                    required property real end_seconds
                                                    required property bool included
                                                    required property int sort_order
                                                    required property int index
                                                    width: 260
                                                    height: ListView.view.height
                                                    radius: 8
                                                    color: "#ffffff"
                                                    border.color: included ? "#0f9f6e" : "#e2e8f0"

                                                    MouseArea {
                                                        anchors.fill: parent
                                                        acceptedButtons: Qt.LeftButton
                                                        onClicked: function(mouse) {
                                                            scenicAppRoot.setSegmentSelectionAt(index, !included, mouse.modifiers)
                                                        }
                                                    }

                                                    ColumnLayout {
                                                        anchors.fill: parent
                                                        anchors.margins: 10
                                                        spacing: 8
                                                        Rectangle {
                                                            Layout.fillWidth: true
                                                            Layout.fillHeight: true
                                                            radius: 6
                                                            color: "#101820"
                                                            clip: true
                                                            VideoOutput {
                                                                id: segmentOutput
                                                                anchors.fill: parent
                                                                anchors.margins: 4
                                                            }
                                                            MediaPlayer {
                                                                id: segmentPlayer
                                                                videoOutput: segmentOutput
                                                                property int pendingPosition: -1
                                                                source: ""
                                                                onMediaStatusChanged: {
                                                                    if (pendingPosition >= 0 && (mediaStatus === MediaPlayer.LoadedMedia || mediaStatus === MediaPlayer.BufferedMedia)) {
                                                                        var targetPosition = pendingPosition
                                                                        pendingPosition = -1
                                                                        setPosition(targetPosition)
                                                                        play()
                                                                    }
                                                                }
                                                            }
                                                            Image {
                                                                anchors.fill: parent
                                                                anchors.margins: 4
                                                                source: segmentPlayer.playbackState === MediaPlayer.StoppedState && thumbnail_url ? app.absoluteUrl(thumbnail_url) : ""
                                                                fillMode: Image.PreserveAspectCrop
                                                                asynchronous: true
                                                            }
                                                            Text {
                                                                anchors.centerIn: parent
                                                                visible: !original_media_url && !thumbnail_url
                                                                text: "无片段"
                                                                color: "#94a3b8"
                                                            }
                                                        }
                                                        Text { text: camera_name + " · " + scenicAppRoot.segmentDurationLabel(start_seconds, end_seconds); color: "#0f172a"; font.pixelSize: 12; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                                        Text { text: "匹配 " + (Number(similarity || 0) * 100).toFixed(1) + "% · " + timestamp; color: "#64748b"; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true }
                                                        RowLayout {
                                                            Layout.fillWidth: true
                                                            AppButton {
                                                                text: "播放片段"
                                                                compact: true
                                                                highlighted: true
                                                                enabled: !!original_media_url
                                                                onClicked: {
                                                                    var startPosition = Math.round(Number(start_seconds || 0) * 1000)
                                                                    segmentPlayer.stop()
                                                                    segmentPlayer.pendingPosition = startPosition
                                                                    segmentPlayer.source = app.absoluteUrl(original_media_url)
                                                                    segmentPlayer.setPosition(startPosition)
                                                                    segmentPlayer.play()
                                                                }
                                                            }
                                                            CheckBox {
                                                                text: "用于成片"
                                                                checked: included
                                                                onClicked: scenicAppRoot.updateSegmentIncluded(segment_id, checked, sort_order, index, 0)
                                                            }
                                                        }
                                                    }
                                                }

                                                Text {
                                                    anchors.centerIn: parent
                                                    visible: scenicSegmentModel.count === 0
                                                    text: scenicAppRoot.hasSelectedSession() ? "暂无匹配片段" : "请选择游客"
                                                    color: "#64748b"
                                                }
                                            }
                                        }
                                    }

                                    ListView {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        model: scenicVlogModel
                                        clip: true
                                        spacing: 12
                                        delegate: Rectangle {
                                            required property int vlog_id
                                            required property string status
                                            required property string preview_url
                                            required property string full_url
                                            required property string preview_filename
                                            required property string full_filename
                                            required property int segment_count
                                            required property real duration_seconds
                                            required property string error
                                            width: ListView.view.width
                                            height: 210
                                            radius: 8
                                            color: "#f8fafc"
                                            border.color: "#dbe4ee"

                                            function playVlogSource(sourceUrl) {
                                                if (!sourceUrl) {
                                                    return
                                                }
                                                vlogPlayer.stop()
                                                vlogPlayer.pendingPosition = 0
                                                vlogPlayer.source = app.absoluteUrl(sourceUrl)
                                                vlogPlayer.setPosition(0)
                                                vlogPlayer.play()
                                            }

                                            RowLayout {
                                                anchors.fill: parent
                                                anchors.margins: 12
                                                spacing: 16

                                                Rectangle {
                                                    Layout.preferredWidth: 300
                                                    Layout.fillHeight: true
                                                    radius: 6
                                                    color: "#101820"
                                                    clip: true
                                                    VideoOutput {
                                                        id: vlogOutput
                                                        anchors.fill: parent
                                                        anchors.margins: 6
                                                    }
                                                    MediaPlayer {
                                                        id: vlogPlayer
                                                        videoOutput: vlogOutput
                                                        property int pendingPosition: -1
                                                        source: preview_url ? app.absoluteUrl(preview_url) : ""
                                                        onMediaStatusChanged: {
                                                            if (pendingPosition >= 0 && (mediaStatus === MediaPlayer.LoadedMedia || mediaStatus === MediaPlayer.BufferedMedia)) {
                                                                var targetPosition = pendingPosition
                                                                pendingPosition = -1
                                                                setPosition(targetPosition)
                                                                play()
                                                            }
                                                        }
                                                    }
                                                    Text {
                                                        anchors.centerIn: parent
                                                        visible: !preview_url
                                                        text: status === "failed" ? "生成失败" : "等待生成"
                                                        color: "#94a3b8"
                                                        font.pixelSize: 14
                                                    }
                                                }

                                                ColumnLayout {
                                                    Layout.fillWidth: true
                                                    Layout.fillHeight: true
                                                    spacing: 8
                                                    Text { text: "短片任务 #" + vlog_id; color: "#0f172a"; font.pixelSize: 18; font.bold: true }
                                                    Text { text: "状态 " + scenicAppRoot.jobLabel(status) + " · 片段 " + String(segment_count || 0) + " · 时长 " + String(duration_seconds || 0) + "s"; color: "#64748b"; font.pixelSize: 13 }
                                                    Text { text: error || ""; color: "#dc2626"; font.pixelSize: 12; visible: !!error }
                                                    Item { Layout.fillHeight: true }
                                                    RowLayout {
                                                        Layout.fillWidth: true
                                                        AppButton {
                                                            text: "预览"
                                                            highlighted: true
                                                            enabled: !!preview_url
                                                            onClicked: playVlogSource(preview_url)
                                                        }
                                                        AppButton {
                                                            text: "完整观看"
                                                            subtle: true
                                                            enabled: !!(full_url || preview_url)
                                                            onClicked: playVlogSource(full_url || preview_url)
                                                        }
                                                        AppButton {
                                                            text: "下载视频"
                                                            subtle: true
                                                            enabled: !!(full_url || preview_url)
                                                            onClicked: app.requestDownload(full_url || preview_url, full_filename || preview_filename || ("scenic_vlog_" + vlog_id + ".mp4"))
                                                        }
                                                        InputField {
                                                            id: paymentAmountField
                                                            Layout.preferredWidth: 112
                                                            placeholderText: "金额（分）"
                                                            inputMethodHints: Qt.ImhDigitsOnly
                                                            validator: IntValidator { bottom: 1 }
                                                        }
                                                        AppButton {
                                                            text: "创建微信支付"
                                                            subtle: true
                                                            enabled: status === "succeeded" && Number(paymentAmountField.text) > 0
                                                            onClicked: client.createWechatScenicPayment(
                                                                scenicAppRoot.selectedSessionId(),
                                                                Number(paymentAmountField.text)
                                                            )
                                                        }
                                                    }
                                                }
                                            }
                                        }

                                        Text {
                                            anchors.centerIn: parent
                                            visible: scenicVlogModel.count === 0
                                            text: scenicAppRoot.hasSelectedSession() ? "暂无短片任务" : "请选择游客"
                                            color: "#64748b"
                                            font.pixelSize: 18
                                        }
                                    }

                                    Rectangle {
                                        Layout.fillWidth: true
                                        Layout.preferredHeight: 112
                                        radius: 8
                                        color: "#f8fafc"
                                        border.color: "#dbe4ee"

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: 10
                                            spacing: 8
                                            RowLayout {
                                                Layout.fillWidth: true
                                                Text { text: "订单与交付"; color: "#0f172a"; font.pixelSize: 14; font.bold: true; Layout.fillWidth: true }
                                                Text { text: String(scenicOrderModel.count) + " 单"; color: "#64748b"; font.pixelSize: 12 }
                                            }
                                            ListView {
                                                Layout.fillWidth: true
                                                Layout.fillHeight: true
                                                model: scenicOrderModel
                                                clip: true
                                                spacing: 6
                                                delegate: Rectangle {
                                                    required property int order_id
                                                    required property string order_no
                                                    required property string status
                                                    required property string payment_provider
                                                    required property string provider_trade_state
                                                    required property string provider_trade_no
                                                    required property string payment_error
                                                    required property string payment_code
                                                    required property string preview_url
                                                    required property string full_url
                                                    width: ListView.view.width
                                                    height: 48
                                                    radius: 6
                                                    color: "#ffffff"
                                                    border.color: "#e2e8f0"
                                                    RowLayout {
                                                        anchors.fill: parent
                                                        anchors.margins: 8
                                                        Text {
                                                            text: (order_no || ("订单 #" + order_id)) + " · " + (provider_trade_state || status)
                                                            color: status === "paid" ? "#0f8a5f" : "#0f172a"
                                                            font.pixelSize: 12
                                                            font.bold: true
                                                            elide: Text.ElideRight
                                                            Layout.fillWidth: true
                                                        }
                                                        Text {
                                                            text: provider_trade_no || payment_error || payment_code || ""
                                                            color: "#64748b"
                                                            font.pixelSize: 11
                                                            elide: Text.ElideRight
                                                            Layout.preferredWidth: 150
                                                        }
                                                        AppButton {
                                                            text: "刷新"
                                                            compact: true
                                                            enabled: payment_provider === "wechat_native" && status !== "paid"
                                                            onClicked: client.refreshScenicOrderPayment(order_id)
                                                        }
                                                        AppButton {
                                                            text: "下载"
                                                            compact: true
                                                            enabled: !!(full_url || preview_url)
                                                            onClicked: app.requestDownload(full_url || preview_url, "scenic_order_" + order_id + ".mp4")
                                                        }
                                                    }
                                                }
                                                Text {
                                                    anchors.centerIn: parent
                                                    visible: scenicOrderModel.count === 0
                                                    text: "创建微信支付订单后会显示支付与交付信息"
                                                    color: "#64748b"
                                                    font.pixelSize: 12
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 20
                            spacing: 12

                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "检索记录"; color: "#0f172a"; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
                                AppButton {
                                    text: "刷新历史"
                                    subtle: true
                                    onClicked: {
                                        client.fetchTourists()
                                        client.fetchHistory()
                                    }
                                }
                            }

                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: 178
                                radius: 8
                                color: "#ffffff"
                                border.color: "#dbe4ee"

                                ColumnLayout {
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    spacing: 8
                                    RowLayout {
                                        Layout.fillWidth: true
                                        Text { text: "游客记录"; color: "#0f172a"; font.pixelSize: 15; font.bold: true; Layout.fillWidth: true }
                                        Text { text: String(scenicAppRoot.touristRows.length) + " 条"; color: "#64748b"; font.pixelSize: 12 }
                                    }
                                    ListView {
                                        Layout.fillWidth: true
                                        Layout.fillHeight: true
                                        model: scenicAppRoot.touristRows
                                        orientation: ListView.Horizontal
                                        clip: true
                                        spacing: 10
                                        delegate: TouristRow {
                                            required property var modelData
                                            width: 310
                                            app: scenicAppRoot.app
                                            rowModel: modelData || ({})
                                            selected: scenicAppRoot.hasSelectedSession() && client.selectedTouristSession.id === modelData.id
                                            onPicked: function(sessionId) {
                                                client.selectTouristSession(sessionId)
                                                scenicAppRoot.activeTab = "matching"
                                            }
                                        }
                                    }
                                }
                            }

                            ListView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                model: scenicHistoryModel
                                clip: true
                                spacing: 12
                                delegate: Rectangle {
                                    required property var content
                                    required property var created_at
                                    required property var query_object_url
                                    required property var result_count
                                    required property var results
                                    width: ListView.view.width
                                    height: 150
                                    radius: 8
                                    color: "#ffffff"
                                    border.color: "#dbe4ee"

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 12
                                        spacing: 14

                                        Rectangle {
                                            Layout.preferredWidth: 110
                                            Layout.fillHeight: true
                                            radius: 6
                                            color: "#f1f5f9"
                                            clip: true
                                            Image {
                                                anchors.fill: parent
                                                anchors.margins: 6
                                                source: query_object_url ? app.absoluteUrl(query_object_url) : ""
                                                fillMode: Image.PreserveAspectFit
                                                asynchronous: true
                                            }
                                            Text {
                                                anchors.centerIn: parent
                                                visible: !query_object_url
                                                text: "Query"
                                                color: "#64748b"
                                            }
                                        }

                                        ColumnLayout {
                                            Layout.preferredWidth: 260
                                            Layout.fillHeight: true
                                            Text { text: content || "景区检索"; color: "#0f172a"; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                            Text { text: created_at || ""; color: "#64748b"; font.pixelSize: 12 }
                                            Text { text: "结果 " + String(result_count || 0); color: "#2563eb"; font.pixelSize: 13; font.bold: true }
                                            Item { Layout.fillHeight: true }
                                        }

                                        Flickable {
                                            Layout.fillWidth: true
                                            Layout.fillHeight: true
                                            contentWidth: resultRow.width
                                            clip: true
                                            Row {
                                                id: resultRow
                                                spacing: 8
                                                Repeater {
                                                    model: results || []
                                                    delegate: Rectangle {
                                                        required property var modelData
                                                        width: 96
                                                        height: 126
                                                        radius: 6
                                                        color: "#f8fafc"
                                                        border.color: "#e2e8f0"
                                                        ColumnLayout {
                                                            anchors.fill: parent
                                                            anchors.margins: 6
                                                            spacing: 4
                                                            Image {
                                                                Layout.fillWidth: true
                                                                Layout.fillHeight: true
                                                                source: modelData.path ? app.absoluteUrl(modelData.path) : ""
                                                                fillMode: Image.PreserveAspectCrop
                                                                asynchronous: true
                                                            }
                                                            Text {
                                                                text: "#" + String(modelData.rank || 0) + " " + (Number(modelData.similarity || 0) * 100).toFixed(0) + "%"
                                                                color: "#0f172a"
                                                                font.pixelSize: 11
                                                                horizontalAlignment: Text.AlignHCenter
                                                                Layout.fillWidth: true
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }

                                Text {
                                    anchors.centerIn: parent
                                    visible: scenicHistoryModel.count === 0
                                    text: "暂无游客检索历史"
                                    color: "#64748b"
                                    font.pixelSize: 18
                                }
                            }
                        }
                    }

                    Item {
                        Layout.fillWidth: true
                        Layout.fillHeight: true

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 20
                            spacing: 12

                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: "操作审计"; color: "#0f172a"; font.pixelSize: 18; font.bold: true; Layout.fillWidth: true }
                                AppButton { text: "刷新审计"; subtle: true; onClicked: client.fetchAudit() }
                            }

                            ListView {
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                model: scenicAuditModel
                                clip: true
                                spacing: 8
                                delegate: Rectangle {
                                    required property var action
                                    required property var username
                                    required property var resource_type
                                    required property var resource_id
                                    required property var created_at
                                    required property var detail
                                    width: ListView.view.width
                                    height: 72
                                    radius: 8
                                    color: "#ffffff"
                                    border.color: "#dbe4ee"

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.margins: 12
                                        spacing: 14
                                        Rectangle {
                                            Layout.preferredWidth: 44
                                            Layout.preferredHeight: 44
                                            radius: 8
                                            color: "#edf7f3"
                                            Text { anchors.centerIn: parent; text: "审"; color: "#0f8a5f"; font.bold: true }
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 2
                                            Text { text: action || "operation"; color: "#0f172a"; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                            Text { text: (username || "") + " · " + (resource_type || "") + " " + (resource_id || "") + " · " + (created_at || ""); color: "#64748b"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                                        }
                                        Text {
                                            text: detail || ""
                                            color: "#506070"
                                            font.pixelSize: 12
                                            elide: Text.ElideRight
                                            Layout.preferredWidth: 360
                                        }
                                    }
                                }

                                Text {
                                    anchors.centerIn: parent
                                    visible: scenicAuditModel.count === 0
                                    text: "暂无审计记录"
                                    color: "#64748b"
                                    font.pixelSize: 18
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        visible: !client.isLoggedIn
        color: "#f4f7fb"

        ColumnLayout {
            anchors.centerIn: parent
            width: 420
            spacing: 16

            Text {
                text: "景区运营平台"
                font.pixelSize: 28
                font.bold: true
                color: "#0f172a"
                Layout.alignment: Qt.AlignHCenter
            }

            Text {
                text: scenicRegisterMode ? "创建景区独立账号" : "登录景区独立账号"
                font.pixelSize: 15
                color: "#64748b"
                Layout.alignment: Qt.AlignHCenter
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: scenicRegisterMode ? 330 : 210
                radius: 8
                color: "#ffffff"
                border.color: "#dbe4ee"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 18
                    spacing: 12

                    InputField {
                        id: scenicUsernameField
                        visible: scenicRegisterMode
                        placeholderText: "用户名"
                        Layout.fillWidth: true
                    }
                    InputField {
                        id: scenicAccountField
                        placeholderText: scenicRegisterMode ? "邮箱" : "用户名或邮箱"
                        Layout.fillWidth: true
                    }
                    InputField {
                        id: scenicPasswordField
                        placeholderText: "密码"
                        echoMode: TextInput.Password
                        Layout.fillWidth: true
                    }
                    RowLayout {
                        visible: scenicRegisterMode
                        Layout.fillWidth: true
                        InputField {
                            id: scenicCodeField
                            placeholderText: "邮箱验证码"
                            Layout.fillWidth: true
                        }
                        AppButton {
                            text: "获取验证码"
                            compact: true
                            onClicked: client.requestEmailCode(scenicAccountField.text, "register", scenicUsernameField.text)
                        }
                    }

                    AppButton {
                        text: scenicRegisterMode ? "注册并进入" : "登录"
                        highlighted: true
                        Layout.fillWidth: true
                        onClicked: {
                            if (scenicRegisterMode) {
                                client.registerUser(scenicUsernameField.text, scenicAccountField.text, scenicPasswordField.text, scenicCodeField.text)
                            } else {
                                client.login(scenicAccountField.text, scenicPasswordField.text)
                            }
                        }
                    }
                    AppButton {
                        text: scenicRegisterMode ? "已有账号，去登录" : "注册景区账号"
                        subtle: true
                        Layout.fillWidth: true
                        onClicked: scenicRegisterMode = !scenicRegisterMode
                    }
                }
            }

            AppButton {
                text: "返回启动器"
                subtle: true
                Layout.alignment: Qt.AlignHCenter
                onClicked: app.appMode = "launcher"
            }
        }
    }

    FileDialog {
        id: localGalleryDialog
        title: "选择照片"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp *.bmp)"]
        onAccepted: localEntryImageUrl = selectedFile
    }

    FileDialog {
        id: simulateVideoDialog
        title: "选择用于模拟摄像头的视频文件"
        nameFilters: ["Videos (*.mp4 *.mov *.avi *.mkv)"]
        onAccepted: {
            if (scenicAppRoot.pendingSimulatePointId > 0) {
                simPlayer.source = selectedFile
                simPlayer.play()
                client.uploadScenicSimulationVideo(scenicAppRoot.pendingSimulatePointId, selectedFile)
            }
        }
    }

    component TouristRow: Rectangle {
        required property var app
        property var rowModel: ({})
        property bool selected: false
        signal picked(int sessionId)

        height: 66
        radius: 8
        color: selected ? "#eaf7f0" : "#f8fafc"
        border.color: selected ? "#0f9f6e" : "#e2e8f0"

        TapHandler { onTapped: picked(Number((rowModel && rowModel.id) || 0)) }

        RowLayout {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 10

            Rectangle {
                Layout.preferredWidth: 46
                Layout.preferredHeight: 46
                radius: 6
                color: "#e2e8f0"
                clip: true
                Image {
                    anchors.fill: parent
                    source: rowModel.entry_image_url ? app.absoluteUrl(rowModel.entry_image_url) : ""
                    fillMode: Image.PreserveAspectCrop
                    asynchronous: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text {
                    text: rowModel.visitor_label || rowModel.ticket_code || "游客"
                    color: "#0f172a"
                    font.pixelSize: 14
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Text {
                    text: rowModel.ticket_code + " · " + rowModel.created_at
                    color: "#64748b"
                    font.pixelSize: 11
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }

            Rectangle {
                Layout.preferredWidth: 56
                Layout.preferredHeight: 24
                radius: 12
                color: scenicAppRoot.statusColor(rowModel.status) + "22"
                Text {
                    anchors.centerIn: parent
                    text: scenicAppRoot.statusLabel(rowModel.status)
                    color: scenicAppRoot.statusColor(rowModel.status)
                    font.pixelSize: 11
                    font.bold: true
                }
            }
        }
    }

    component MatchingPage: Item {
        required property var app
        required property var client
        required property var touristModel
        required property var segmentModel
        property bool selected: false
        signal goVlog()

        RowLayout {
            anchors.fill: parent
            anchors.margins: 20
            spacing: 16

            Rectangle {
                Layout.preferredWidth: 320
                Layout.fillHeight: true
                radius: 8
                color: "#ffffff"
                border.color: "#dbe4ee"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 14
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: "游客"; color: "#0f172a"; font.pixelSize: 16; font.bold: true; Layout.fillWidth: true }
                        AppButton { text: "刷新"; compact: true; subtle: true; onClicked: client.fetchTourists() }
                    }
                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: scenicAppRoot.touristRows
                        clip: true
                        spacing: 8
                        delegate: TouristRow {
                            required property var modelData
                            width: ListView.view.width
                            app: scenicAppRoot.app
                            rowModel: modelData || ({})
                            selected: scenicAppRoot.hasSelectedSession() && client.selectedTouristSession.id === modelData.id
                            onPicked: function(sessionId) { client.selectTouristSession(sessionId) }
                        }
                    }
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                radius: 8
                color: "#ffffff"
                border.color: "#dbe4ee"

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 14

                    RowLayout {
                        Layout.fillWidth: true
                        Rectangle {
                            Layout.preferredWidth: 74
                            Layout.preferredHeight: 74
                            radius: 8
                            color: "#f1f5f9"
                            clip: true
                            Image {
                                anchors.fill: parent
                                source: selected ? app.absoluteUrl(client.selectedTouristSession.entry_image_url) : ""
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Text {
                                text: selected ? (client.selectedTouristSession.visitor_label || client.selectedTouristSession.ticket_code) : "请选择游客"
                                color: "#0f172a"
                                font.pixelSize: 20
                                font.bold: true
                            }
                            Text {
                                text: selected ? ("票号 " + client.selectedTouristSession.ticket_code + " · " + scenicAppRoot.statusLabel(client.selectedTouristSession.status)) : "选择后可基于入园照片匹配监控视频片段"
                                color: "#64748b"
                                font.pixelSize: 13
                            }
                        }
                        AppButton {
                            text: "开始匹配"
                            highlighted: true
                            enabled: selected
                            onClicked: client.matchTourist(scenicAppRoot.selectedSessionId())
                        }
                        AppButton {
                            text: "流转短片"
                            subtle: true
                            enabled: selected
                            onClicked: goVlog()
                        }
                    }

                    Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: "#e2e8f0" }

                    RowLayout {
                        Layout.fillWidth: true
                        Text {
                            text: "已选 " + String(scenicAppRoot.includedSegmentCount()) + " / " + String(segmentModel.count)
                            color: "#64748b"
                            font.pixelSize: 12
                            Layout.fillWidth: true
                        }
                        AppButton { text: "全选"; compact: true; subtle: true; enabled: segmentModel.count > 0; onClicked: scenicAppRoot.setAllSegmentsIncluded(true) }
                        AppButton { text: "清空"; compact: true; subtle: true; enabled: segmentModel.count > 0; onClicked: scenicAppRoot.setAllSegmentsIncluded(false) }
                        AppButton { text: "反选"; compact: true; subtle: true; enabled: segmentModel.count > 0; onClicked: scenicAppRoot.invertSegmentSelection() }
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        model: segmentModel
                        clip: true
                        spacing: 10
                        delegate: Rectangle {
                            required property int segment_id
                            required property string camera_name
                            required property string timestamp
                            required property real similarity
                            required property string thumbnail_url
                            required property real start_seconds
                            required property real end_seconds
                            required property bool included
                            required property int sort_order
                            required property int index
                            width: ListView.view.width
                            height: 124
                            radius: 8
                            color: "#f8fafc"
                            border.color: "#dbe4ee"

                            MouseArea {
                                anchors.fill: parent
                                acceptedButtons: Qt.LeftButton
                                onClicked: function(mouse) {
                                    scenicAppRoot.setSegmentSelectionAt(index, !included, mouse.modifiers)
                                }
                            }

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 10
                                spacing: 12
                                Rectangle {
                                    Layout.preferredWidth: 130
                                    Layout.fillHeight: true
                                    radius: 6
                                    color: "#e2e8f0"
                                    clip: true
                                    Image {
                                        anchors.fill: parent
                                        source: thumbnail_url ? app.absoluteUrl(thumbnail_url) : ""
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 4
                                    Text { text: camera_name || "未知点位"; color: "#0f172a"; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text { text: "时间 " + (timestamp || "") + " · 匹配 " + (Number(similarity || 0) * 100).toFixed(1) + "%"; color: "#64748b"; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text { text: "片段 " + scenicAppRoot.segmentDurationLabel(start_seconds, end_seconds); color: "#506070"; font.pixelSize: 12 }
                                    Text { text: included ? "已加入成片" : "不用于成片"; color: included ? "#0f8a5f" : "#94a3b8"; font.pixelSize: 12 }
                                }
                                CheckBox {
                                    text: "用于成片"
                                    checked: included
                                    onClicked: scenicAppRoot.updateSegmentIncluded(segment_id, checked, sort_order, index, 0)
                                }
                            }
                        }

                        Text {
                            anchors.centerIn: parent
                            visible: segmentModel.count === 0
                            text: selected ? "暂无匹配片段" : "请选择游客"
                            color: "#64748b"
                            font.pixelSize: 18
                        }
                    }
                }
            }
        }
    }
}
