pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: contentRoot
    required property var app
    required property var client
    required property var resultModel
    property alias searchText: searchInput.text

    function clearSearchInput() {
        searchInput.clear()
    }

    function setSearchText(text) {
        searchInput.text = text || ""
    }

    function selectedTouristId() {
        return client.selectedTouristSession && client.selectedTouristSession.id ? Number(client.selectedTouristSession.id) : 0
    }

    function selectedSegments() {
        return client.selectedTouristSession && client.selectedTouristSession.segments ? client.selectedTouristSession.segments : []
    }

    function selectedVlogs() {
        return client.selectedTouristSession && client.selectedTouristSession.vlog_jobs ? client.selectedTouristSession.vlog_jobs : []
    }

    function selectedOrders() {
        return client.selectedTouristSession && client.selectedTouristSession.orders ? client.selectedTouristSession.orders : []
    }

    function latestOrder() {
        var orders = selectedOrders()
        return orders.length > 0 ? orders[0] : ({})
    }

    function latestPendingOrderId() {
        var orders = selectedOrders()
        for (var i = 0; i < orders.length; i++) {
            if (orders[i].status === "pending")
                return Number(orders[i].id || 0)
        }
        return 0
    }

    function latestPreviewUrl() {
        var orders = selectedOrders()
        for (var i = 0; i < orders.length; i++) {
            if (orders[i].preview_url)
                return orders[i].preview_url
        }
        var vlogs = selectedVlogs()
        return vlogs.length > 0 ? (vlogs[0].preview_url || "") : ""
    }

    function latestFullUrl() {
        var orders = selectedOrders()
        for (var i = 0; i < orders.length; i++) {
            if (orders[i].status === "paid" && orders[i].full_url)
                return orders[i].full_url
        }
        return ""
    }

    Timer {
        interval: 3000
        repeat: true
        running: app.currentPage === "scenic" && client.isLoggedIn && client.currentProjectId > 0
        onTriggered: {
            client.fetchScenicDashboard()
            client.fetchScenicCameraStatus()
            client.fetchTourists()
            client.fetchJobs()
            if (selectedTouristId() > 0)
                client.selectTouristSession(selectedTouristId())
        }
    }
    Layout.fillWidth: true
    Layout.fillHeight: true
    color: "transparent"

    ScrollView {
        id: contentScroll
        anchors.fill: parent
        anchors.margins: app.contentOuterMargin
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            width: Math.min(contentScroll.availableWidth, app.contentMaximumWidth)
            x: Math.max(0, (contentScroll.availableWidth - width) / 2)
            spacing: 18

            ColumnLayout {
                visible: app.currentPage === "projects"
                Layout.fillWidth: true
                spacing: 16

                PageTitle {
                    title: "项目列表"
                    subtitle: "管理项目空间、媒体接入和检索上下文。"
                    actionText: "新建项目"
                    actionEnabled: client.isLoggedIn
                    onActionTriggered: app.openProjectDialog()
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 120
                    radius: 8
                    color: "#0F172A"
                    border.color: "#243149"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 18
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                text: client.isLoggedIn ? "检索空间已准备" : "登录后开始创建检索项目"
                                color: "white"
                                font.pixelSize: 20
                                font.bold: true
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: client.isLoggedIn ? "从项目进入媒体管理，上传图片或视频后即可建立索引并进行跨模态检索。" : "当前处于未登录状态，先登录即可同步项目、历史和审计信息。"
                                color: "#bcc9da"
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                        }
                        MetricTile {
                            Layout.preferredWidth: 128
                            title: "项目"
                            value: String(client.projectList.length)
                            accentColor: "#818CF8"
                            compact: true
                        }
                        MetricTile {
                            Layout.preferredWidth: 128
                            title: "历史"
                            value: String(client.historyList.length)
                            accentColor: "#2fbf8f"
                            compact: true
                        }
                    }
                }

                EmptyState {
                    visible: client.projectList.length === 0
                    title: client.isLoggedIn ? "暂无项目" : "尚未登录"
                    subtitle: client.isLoggedIn ? "创建第一个项目后，媒体和检索结果会按项目隔离。" : "登录或注册后会自动创建默认检索项目。"
                }

                Repeater {
                    model: client.projectList
                    delegate: Rectangle {
                        id: projectCard
                        required property var modelData
                        Layout.fillWidth: true
                        height: 104
                        radius: 8
                        color: modelData.id === client.currentProjectId ? "#EEF2FF" : (projectHover.hovered ? "#FFFFFF" : "#ffffff")
                        border.color: modelData.id === client.currentProjectId ? "#818CF8" : "#E2E8F0"
                        border.width: modelData.id === client.currentProjectId ? 1.4 : 1

                        HoverHandler { id: projectHover }

                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 16
                            spacing: 14

                            Rectangle {
                                Layout.preferredWidth: 48
                                Layout.preferredHeight: 48
                                radius: 8
                                color: modelData.id === client.currentProjectId ? "#4F46E5" : "#F1F5F9"
                                Text {
                                    anchors.centerIn: parent
                                    text: modelData.project_type === "camera" ? "相" : "项"
                                    color: modelData.id === client.currentProjectId ? "white" : "#64748B"
                                    font.pixelSize: 18
                                    font.bold: true
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 5
                                Text {
                                    text: modelData.name
                                    color: "#0F172A"
                                    font.bold: true
                                    font.pixelSize: 17
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: app.projectTypeLabel(modelData.project_type) + "  |  " + (modelData.description || "无描述")
                                    color: "#64748B"
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: "所有者 " + modelData.owner_username + "  |  创建于 " + modelData.created_at
                                    color: "#94A3B8"
                                    font.pixelSize: 12
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }

                            AppButton {
                                text: modelData.id === client.currentProjectId ? "当前项目" : "进入"
                                highlighted: modelData.id !== client.currentProjectId
                                subtle: modelData.id === client.currentProjectId
                                onClicked: {
                                    client.selectProject(modelData.id)
                                    app.currentPage = modelData.project_type === "camera" ? "camera" : "detail"
                                }
                            }
                            AppButton {
                                text: "删除"
                                subtle: true
                                danger: true
                                onClicked: app.openConfirm("deleteProject", modelData.id, "确认删除项目", "确定要删除项目“" + modelData.name + "”吗？项目内媒体、索引和关联任务会一并清理。")
                            }
                        }
                    }
                }
            }


            ColumnLayout {
                visible: app.currentPage === "detail"
                Layout.fillWidth: true
                spacing: 16

                PageTitle {
                    title: "项目详情"
                    subtitle: "查看项目资产、索引状态和下一步操作。"
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    MetricTile { title: "项目"; value: String(client.projectList.length); accentColor: "#4F46E5" }
                    MetricTile { title: "媒体"; value: String(client.mediaList.length); accentColor: "#16a163" }
                    MetricTile { title: "任务"; value: String(client.jobList.length); accentColor: "#d38b17" }
                    MetricTile { title: "历史"; value: String(client.historyList.length); accentColor: "#8b5cf6" }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 172
                    color: "#ffffff"
                    radius: 8
                    border.color: "#E2E8F0"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 18
                        spacing: 18

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Text {
                                text: client.currentProjectName || "未选择项目"
                                color: "#0F172A"
                                font.bold: true
                                font.pixelSize: 19
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: app.isCameraProject() ? (client.cameraRunning ? "统一监控在线，检测到的行人会持续加入当前项目索引。" : "统一监控会持续提供画面，启用实时入库后才写入当前项目索引。") : (client.mediaList.length > 0 ? "媒体已接入，可以进入检索页进行以文搜图或以图搜图。" : "下一步：上传图片或视频，系统完成索引后即可检索。")
                                color: "#64748B"
                                font.pixelSize: 13
                                wrapMode: Text.WordWrap
                                Layout.fillWidth: true
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                AppButton {
                                    visible: app.isCameraProject()
                                    text: client.cameraRunning ? "查看实时入库" : "启用实时入库"
                                    highlighted: true
                                    onClicked: {
                                        app.currentPage = "camera"
                                        if (!client.cameraRunning)
                                            client.startCamera(client.cameraSource || "0")
                                    }
                                }
                                AppButton {
                                    text: "上传图片"
                                    highlighted: !app.isCameraProject() && client.mediaList.length === 0
                                    onClicked: {
                                        app.currentPage = "media"
                                        app.isAppendMode = true
                                        app.openGalleryDialog()
                                    }
                                }
                                AppButton {
                                    text: "上传视频"
                                    onClicked: {
                                        app.currentPage = "media"
                                        app.isAppendMode = true
                                        app.openVideoDialog()
                                    }
                                }
                                AppButton {
                                    text: "打开检索页"
                                    enabled: client.mediaList.length > 0 || app.isSystemReady
                                    highlighted: app.isSystemReady
                                    onClicked: app.currentPage = "search"
                                }
                                AppButton {
                                    text: "删除项目"
                                    danger: true
                                    subtle: true
                                    onClicked: app.openConfirm("deleteProject", client.currentProjectId, "确认删除项目", "确定要删除当前项目“" + client.currentProjectName + "”吗？项目内媒体、索引和关联任务会一并清理。")
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }

                        Row {
                            spacing: 8
                            Repeater {
                                model: client.previewList
                                delegate: Rectangle {
                                    required property var modelData
                                    width: 64
                                    height: 64
                                    radius: 8
                                    color: "#F1F5F9"
                                    clip: true
                                    Image {
                                        anchors.fill: parent
                                        source: modelData
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                    }
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "media"
                Layout.fillWidth: true
                spacing: 14

                PageTitle {
                    title: "媒体管理"
                    subtitle: "上传图库图片或监控视频，后台会自动抽取目标并建立索引。"
                    actionText: "上传图片"
                    actionEnabled: client.currentProjectId > 0
                    onActionTriggered: {
                        app.isAppendMode = true
                        app.openGalleryDialog()
                    }
                    secondaryActionText: "上传视频"
                    secondaryActionEnabled: client.currentProjectId > 0
                    onSecondaryActionTriggered: {
                        app.isAppendMode = true
                        app.openVideoDialog()
                    }
                }

                RowLayout {
                    visible: client.mediaList.length > 0
                    Layout.fillWidth: true
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    AppButton {
                        text: "一键清空媒体"
                        danger: true
                        subtle: true
                        onClicked: app.openConfirm("clearMedia", 0, "确认清空媒体", "确定要清空当前项目的全部媒体吗？对应索引和识别目标会一并删除。")
                    }
                }

                EmptyState {
                    visible: client.mediaList.length === 0
                    title: "暂无媒体"
                    subtitle: "上传图片或视频后，媒体状态、目标数和创建时间会显示在这里。"
                }

                TableHeader {
                    visible: client.mediaList.length > 0
                    columns: ["预览", "文件名", "类型", "大小", "状态", "目标数", "创建时间", "操作"]
                    widths: [82, 260, 64, 86, 92, 70, 140, 168]
                }

                Repeater {
                    model: client.mediaList
                    delegate: MediaRow {
                                    app: contentRoot.app
                        required property var modelData
                        media: modelData
                        onDeleteRequested: mediaId => app.openConfirm("deleteMedia", mediaId, "确认删除媒体", "确定要删除媒体“" + modelData.filename + "”吗？对应索引和识别目标会一并删除。")
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "camera"
                Layout.fillWidth: true
                spacing: 14
                onVisibleChanged: {
                    if (visible && app.isCameraProject()) {
                        client.fetchCameras()
                        client.fetchCameraStatus()
                    }
                }

                PageTitle {
                    title: "实时相机"
                    subtitle: client.cameraRunning ? "YOLO 正在消费统一监控画面，检测结果会持续加入当前项目索引。" : "统一监控独立运行；这里的开关只控制是否将检测结果写入当前项目索引。"
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    MetricTile { title: "入库"; value: client.cameraRunning ? "进行中" : "已暂停"; accentColor: client.cameraRunning ? "#16a163" : "#d38b17" }
                    MetricTile { title: "监控"; value: client.cameraMonitorOnline ? "在线" : "等待"; accentColor: client.cameraMonitorOnline ? "#0f9f6e" : "#d38b17" }
                    MetricTile { title: "目标"; value: String(client.cameraTargetCount); accentColor: "#4F46E5" }
                    MetricTile { title: "源"; value: client.cameraSource || "0"; accentColor: "#8b5cf6" }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 540
                    radius: 8
                    color: "#ffffff"
                    border.color: "#E2E8F0"

                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 14

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            radius: 8
                            color: "#111827"
                            clip: true

                            Image {
                                anchors.fill: parent
                                anchors.margins: 10
                                source: client.cameraFrameUrl.length > 0 ? app.absoluteUrl(client.cameraFrameUrl) + "?v=" + client.cameraFrameVersion : ""
                                fillMode: Image.PreserveAspectFit
                                asynchronous: true
                            }

                            EmptyState {
                                anchors.centerIn: parent
                                width: 420
                                visible: client.cameraFrameUrl.length === 0
                                title: client.cameraMonitorOnline ? "等待实时画面" : "等待统一监控"
                                subtitle: client.cameraMessage
                            }
                        }

                        Rectangle {
                            Layout.preferredWidth: 300
                            Layout.fillHeight: true
                            radius: 8
                            color: "#f8fafc"
                            border.color: "#E2E8F0"

                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 14
                                spacing: 12

                                Text {
                                    text: "采集控制"
                                    color: "#0F172A"
                                    font.pixelSize: 16
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                                ComboBox {
                                    id: cameraDeviceCombo
                                    Layout.fillWidth: true
                                    model: client.availableCameraList
                                    textRole: "label"
                                    valueRole: "source"
                                    enabled: !client.cameraRunning && count > 0
                                    onActivated: cameraSourceInput.text = String(currentValue)
                                    background: Rectangle {
                                        radius: 7
                                        color: cameraDeviceCombo.enabled ? "#ffffff" : "#F1F5F9"
                                        border.color: "#E2E8F0"
                                    }
                                    contentItem: Text {
                                        text: cameraDeviceCombo.count > 0 ? cameraDeviceCombo.displayText : "未检测到可用摄像头"
                                        color: cameraDeviceCombo.count > 0 ? "#0F172A" : "#94A3B8"
                                        font.pixelSize: 13
                                        verticalAlignment: Text.AlignVCenter
                                        elide: Text.ElideRight
                                        leftPadding: 12
                                    }
                                }
                                InputField {
                                    id: cameraSourceInput
                                    text: client.cameraSource || "0"
                                    placeholderText: "摄像头编号或流地址"
                                    enabled: !client.cameraRunning
                                    Layout.fillWidth: true
                                }
                                AppButton {
                                    text: client.cameraRunning ? "暂停实时入库" : "启用实时入库"
                                    highlighted: !client.cameraRunning
                                    Layout.fillWidth: true
                                    onClicked: {
                                        if (client.cameraRunning)
                                            client.stopCamera()
                                        else
                                            client.startCamera(cameraSourceInput.text)
                                    }
                                }
                                AppButton {
                                    text: "刷新状态"
                                    subtle: true
                                    Layout.fillWidth: true
                                    onClicked: {
                                        client.fetchCameras()
                                        client.fetchCameraStatus()
                                    }
                                }
                                AppButton {
                                    text: "下载录像"
                                    subtle: true
                                    enabled: !client.cameraRunning && client.cameraRecordingUrl.length > 0
                                    Layout.fillWidth: true
                                    onClicked: app.requestDownload(client.cameraRecordingUrl, client.cameraRecordingName || ("myreid_camera_" + Date.now() + ".mp4"))
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 1
                                    color: "#E2E8F0"
                                }
                                Text {
                                    text: client.cameraMessage
                                    color: "#64748B"
                                    font.pixelSize: 13
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: "入库目标 " + client.cameraTargetCount
                                    color: "#0F172A"
                                    font.pixelSize: 18
                                    font.bold: true
                                    Layout.fillWidth: true
                                }
                                Item { Layout.fillHeight: true }
                                AppButton {
                                    text: "打开检索页"
                                    enabled: app.isSystemReady
                                    highlighted: app.isSystemReady
                                    Layout.fillWidth: true
                                    onClicked: app.currentPage = "search"
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "jobs"
                Layout.fillWidth: true
                spacing: 14

                PageTitle {
                    title: "任务进度"
                    subtitle: "查看媒体处理、抽帧、检测与向量写入状态。"
                }

                EmptyState {
                    visible: client.jobList.length === 0
                    title: "暂无任务"
                    subtitle: "上传媒体后，后台任务会自动出现在这里。"
                }

                Repeater {
                    model: client.jobList
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        height: 104
                        radius: 8
                        color: "#ffffff"
                        border.color: "#E2E8F0"
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 10
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: modelData.type + "  |  " + modelData.status
                                    color: "#0F172A"
                                    font.bold: true
                                    font.pixelSize: 15
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                StatusPill {
                                    text: modelData.status
                                    toneColor: modelData.status === "failed" ? "#d04444" : (modelData.status === "completed" ? "#16a163" : "#d38b17")
                                }
                            }
                            AppProgressBar {
                                Layout.fillWidth: true
                                value: app.jobProgress(modelData.progress)
                            }
                            Text {
                                text: (modelData.message || modelData.error || modelData.id) + "  |  " + modelData.updated_at
                                color: "#64748B"
                                font.pixelSize: 12
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "search"
                Layout.fillWidth: true
                spacing: 14

                PageTitle {
                    title: "检索页"
                    subtitle: "输入文本描述或上传查询图片，快速定位相似目标。"
                }

                Rectangle {
                    visible: !app.isSystemReady
                    Layout.fillWidth: true
                    Layout.preferredHeight: 112
                    color: "#fff8eb"
                    radius: 8
                    border.color: "#f0d49a"
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 14
                        Rectangle {
                            Layout.preferredWidth: 44
                            Layout.preferredHeight: 44
                            radius: 8
                            color: "#f3bf4f"
                            Text {
                                anchors.centerIn: parent
                                text: "!"
                                color: "#583b05"
                                font.pixelSize: 20
                                font.bold: true
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 5
                            Text {
                                text: client.mediaList.length === 0 ? "当前项目还没有可检索媒体" : "媒体正在索引，完成后即可检索"
                                color: "#0F172A"
                                font.bold: true
                                font.pixelSize: 16
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Text {
                                text: client.mediaList.length === 0 ? "先上传图片或视频，系统会自动抽取目标并建立索引。" : "可以在任务进度页查看处理状态。"
                                color: "#72551c"
                                font.pixelSize: 13
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                            }
                        }
                        AppButton {
                            text: "上传图片"
                            highlighted: true
                            onClicked: {
                                app.currentPage = "media"
                                app.isAppendMode = true
                                app.openGalleryDialog()
                            }
                        }
                        AppButton {
                            text: "上传视频"
                            onClicked: {
                                app.currentPage = "media"
                                app.isAppendMode = true
                                app.openVideoDialog()
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 150
                    radius: 8
                    color: "#ffffff"
                    border.color: "#E2E8F0"
                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 14
                        spacing: 12
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            InputField {
                                id: searchInput
                                Layout.fillWidth: true
                                placeholderText: "输入文本描述，例如 A man wears a purple shirt"
                                enabled: app.isSystemReady && !app.searchRefreshing
                                onAccepted: app.submitTextSearch()
                            }
                            AppButton {
                                text: "以文搜图"
                                highlighted: true
                                enabled: app.isSystemReady && !app.searchRefreshing
                                onClicked: app.submitTextSearch()
                            }
                            AppButton {
                                text: "以图搜图"
                                enabled: app.isSystemReady && !app.searchRefreshing
                                onClicked: app.openQueryDialog()
                            }
                            AppButton {
                                text: "导出报告"
                                enabled: client.lastSearchTaskId > 0
                                onClicked: client.exportSearchReport(client.lastSearchTaskId)
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Text {
                                text: "相似度≥" + app.searchMinSimilarity.toFixed(2)
                                color: "#46556a"
                                font.pixelSize: 12
                                Layout.preferredWidth: 92
                                elide: Text.ElideRight
                            }
                            Slider {
                                from: 0
                                to: 1
                                stepSize: 0.01
                                value: app.searchMinSimilarity
                                Layout.preferredWidth: 150
                                enabled: !app.searchRefreshing
                                onMoved: app.searchMinSimilarity = value
                            }
                            InputField {
                                placeholderText: "摄像头 ID"
                                text: app.searchCameraFilter
                                Layout.preferredWidth: 120
                                enabled: !app.searchRefreshing
                                onTextChanged: app.searchCameraFilter = text
                            }
                            InputField {
                                placeholderText: "开始时间"
                                text: app.searchStartTimeFilter
                                Layout.preferredWidth: 132
                                enabled: !app.searchRefreshing
                                onTextChanged: app.searchStartTimeFilter = text
                            }
                            InputField {
                                placeholderText: "结束时间"
                                text: app.searchEndTimeFilter
                                Layout.preferredWidth: 132
                                enabled: !app.searchRefreshing
                                onTextChanged: app.searchEndTimeFilter = text
                            }
                            ComboBox {
                                id: searchMediaCombo
                                Layout.preferredWidth: 190
                                model: client.mediaList
                                textRole: "filename"
                                valueRole: "id"
                                currentIndex: -1
                                enabled: !app.searchRefreshing && count > 0
                                onActivated: app.searchMediaFilter = Number(currentValue || 0)
                                background: Rectangle {
                                    radius: 7
                                    color: searchMediaCombo.enabled ? "#ffffff" : "#F1F5F9"
                                    border.color: "#E2E8F0"
                                }
                                contentItem: Text {
                                    text: app.searchMediaFilter > 0 ? searchMediaCombo.displayText : "全部媒体"
                                    color: searchMediaCombo.enabled ? "#0F172A" : "#94A3B8"
                                    font.pixelSize: 13
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: 12
                                    elide: Text.ElideRight
                                }
                            }
                            AppButton {
                                text: "清除筛选"
                                subtle: true
                                enabled: !app.searchRefreshing
                                onClicked: {
                                    app.searchMinSimilarity = 0
                                    app.searchCameraFilter = ""
                                    app.searchStartTimeFilter = ""
                                    app.searchEndTimeFilter = ""
                                    app.searchMediaFilter = 0
                                    searchMediaCombo.currentIndex = -1
                                }
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 548
                    radius: 8
                    color: "#ffffff"
                    border.color: "#E2E8F0"
                    clip: true

                    EmptyState {
                        anchors.centerIn: parent
                        width: 420
                        visible: !app.searchRefreshing && resultModel.count === 0
                        title: app.isSystemReady ? "暂无检索结果" : "等待索引就绪"
                        subtitle: app.isSystemReady ? "提交文本或图片查询后，结果会以相似度排序展示。" : "索引完成后即可发起检索。"
                    }

                    Rectangle {
                        anchors.centerIn: parent
                        width: 360
                        height: 118
                        radius: 8
                        visible: app.searchRefreshing
                        color: "#ffffff"
                        border.color: "#9ac4ff"
                        z: 2
                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 18
                            spacing: 12
                            Text {
                                text: app.searchRefreshMessage
                                color: "#0F172A"
                                font.pixelSize: 15
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            AppProgressBar {
                                Layout.fillWidth: true
                                value: app.searchRefreshProgress
                            }
                            Text {
                                text: "旧结果已清空，请等待最新结果返回"
                                color: "#657386"
                                font.pixelSize: 12
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }
                    }

                    GridView {
                        anchors.fill: parent
                        anchors.margins: 14
                        model: resultModel
                        cellWidth: 190
                        cellHeight: 370
                        clip: true
                        visible: !app.searchRefreshing && resultModel.count > 0
                        delegate: Rectangle {
                            required property int result_id
                            required property int rank
                            required property string imageUrl
                            required property real similarity
                            required property string source_info
                            required property string media_filename
                            required property string media_type
                            required property string timestamp
                            required property string original_media_url
                            required property real video_seek_seconds
                            width: 178
                            height: 354
                            radius: 8
                            color: "#FFFFFF"
                            border.color: "#E2E8F0"
                            ColumnLayout {
                                anchors.fill: parent
                                anchors.margins: 9
                                spacing: 8
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 160
                                    radius: 8
                                    color: "#F1F5F9"
                                    clip: true
                                    Image {
                                        anchors.fill: parent
                                        source: imageUrl
                                        fillMode: Image.PreserveAspectCrop
                                        asynchronous: true
                                    }
                                }
                                Text {
                                    text: "#" + rank + "  相似度 " + similarity.toFixed(4)
                                    color: "#c43e3e"
                                    font.bold: true
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: source_info
                                    color: "#64748B"
                                    font.pixelSize: 11
                                    wrapMode: Text.WordWrap
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    maximumLineCount: 3
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: (media_filename || app.mediaTypeLabel(media_type)) + (timestamp ? " | " + timestamp : "")
                                    color: "#94A3B8"
                                    font.pixelSize: 10
                                    horizontalAlignment: Text.AlignHCenter
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 6
                                    AppButton {
                                        text: "详情"
                                        compact: true
                                        Layout.fillWidth: true
                                        onClicked: client.fetchSearchResultDetail(result_id)
                                    }
                                    AppButton {
                                        text: "预览"
                                        compact: true
                                        subtle: true
                                        enabled: original_media_url.length > 0
                                        Layout.fillWidth: true
                                        onClicked: app.previewMedia(media_type || "image", media_filename || "原媒体", original_media_url, video_seek_seconds, original_media_url, media_filename || "", imageUrl)
                                    }
                                }
                                AppButton {
                                    text: "下载命中帧"
                                    compact: true
                                    subtle: true
                                    enabled: imageUrl.length > 0
                                    Layout.fillWidth: true
                                    onClicked: app.requestDownload(imageUrl, "myreid_result_" + result_id + ".jpg")
                                }
                            }
                        }
                    }
                }

                RowLayout {
                    visible: resultModel.count > 0 && !app.searchRefreshing
                    Layout.fillWidth: true
                    spacing: 10
                    AppButton {
                        text: "上一页"
                        enabled: client.searchHasPrevious
                        onClicked: requestSearchPage(client.searchPage - 1)
                    }
                    Text {
                        text: "第 " + client.searchPage + " / " + Math.max(1, client.searchTotalPages) + " 页，共 " + client.searchTotal + " 条"
                        color: "#46556a"
                        font.pixelSize: 13
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                    }
                    AppButton {
                        text: "下一页"
                        enabled: client.searchHasNext
                        onClicked: requestSearchPage(client.searchPage + 1)
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "history"
                Layout.fillWidth: true
                spacing: 14

                PageTitle {
                    title: "检索历史"
                    subtitle: "回看最近查询，快速恢复检索结果。"
                }

                EmptyState {
                    visible: client.historyList.length === 0
                    title: "暂无历史"
                    subtitle: "完成一次检索后，查询记录会保存到这里。"
                }

                Repeater {
                    model: client.historyList
                    delegate: Rectangle {
                        required property var modelData
                        Layout.fillWidth: true
                        height: 104
                        radius: 8
                        color: "#ffffff"
                        border.color: "#E2E8F0"
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 12
                            Rectangle {
                                Layout.preferredWidth: 42
                                Layout.preferredHeight: 42
                                radius: 8
                                color: modelData.type === "text" ? "#EEF2FF" : "#ecfdf5"
                                clip: true
                                Image {
                                    anchors.fill: parent
                                    source: modelData.type === "image" ? app.absoluteUrl(modelData.query_object_url || "") : ""
                                    fillMode: Image.PreserveAspectCrop
                                    asynchronous: true
                                    visible: modelData.type === "image" && Boolean(modelData.query_object_url)
                                }
                                Text {
                                    anchors.centerIn: parent
                                    visible: modelData.type !== "image" || !modelData.query_object_url
                                    text: modelData.type === "text" ? "文" : "图"
                                    color: modelData.type === "text" ? "#4F46E5" : "#16a163"
                                    font.pixelSize: 16
                                    font.bold: true
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 5
                                Text {
                                    text: (modelData.type === "text" ? "以文搜图" : "以图搜图") + "  |  " + modelData.created_at
                                    color: "#0F172A"
                                    font.bold: true
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: modelData.content
                                    color: "#64748B"
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: "结果 " + String(modelData.result_count || (modelData.results ? modelData.results.length : 0)) + "  |  筛选 " + (modelData.filter_summary || "无")
                                    color: "#94A3B8"
                                    font.pixelSize: 11
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                            }
                            AppButton {
                                text: "查看结果"
                                onClicked: {
                                    app.selectHistoryItem(modelData)
                                }
                            }
                            AppButton {
                                text: "导出"
                                subtle: true
                                onClicked: client.exportSearchReport(modelData.id)
                            }
                            AppButton {
                                text: "删除"
                                danger: true
                                subtle: true
                                onClicked: client.deleteHistory(modelData.id)
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "users" && client.currentRole === "admin"
                Layout.fillWidth: true
                spacing: 14
                onVisibleChanged: {
                    if (visible)
                        client.fetchAdminUsers()
                }

                PageTitle {
                    title: "用户管理"
                    subtitle: "管理系统用户、全局角色、账号状态和 refresh token。"
                    actionText: "新建用户"
                    actionEnabled: client.currentRole === "admin"
                    onActionTriggered: app.openAdminCreateUserDialog()
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    MetricTile { title: "用户总数"; value: String(client.adminUserList.length); accentColor: "#4F46E5" }
                    MetricTile { title: "启用账号"; value: String(app.activeUserCount()); accentColor: "#16a163" }
                    MetricTile { title: "管理员"; value: String(app.activeAdminUserCount()); accentColor: "#d38b17" }
                }

                EmptyState {
                    visible: client.adminUserList.length === 0
                    title: "暂无用户"
                    subtitle: "点击新建用户创建 operator、viewer 或 admin 账号。"
                }

                TableHeader {
                    visible: client.adminUserList.length > 0
                    columns: ["用户", "邮箱", "角色", "状态", "项目", "Token", "创建时间", "操作"]
                    widths: [160, 220, 120, 100, 70, 90, 160, 310]
                }

                Repeater {
                    model: client.adminUserList
                    delegate: Rectangle {
                        id: adminUserRow
                        required property var modelData
                        readonly property bool isSelf: modelData.username === client.currentUser
                        Layout.fillWidth: true
                        height: 72
                        radius: 8
                        color: adminUserHover.hovered ? "#FFFFFF" : "#ffffff"
                        border.color: "#e1e7ef"

                        HoverHandler { id: adminUserHover }

                        Row {
                            anchors.fill: parent
                            anchors.leftMargin: 14
                            anchors.rightMargin: 14

                            Item {
                                width: 160
                                height: parent.height
                                Column {
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 4
                                    Text {
                                        width: parent.width - 10
                                        text: adminUserRow.modelData.username || "-"
                                        color: "#0F172A"
                                        font.pixelSize: 13
                                        font.bold: true
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        width: parent.width - 10
                                        text: String(adminUserRow.modelData.public_id || "").slice(0, 8)
                                        color: "#94A3B8"
                                        font.pixelSize: 11
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            Text {
                                width: 220
                                height: parent.height
                                text: adminUserRow.modelData.email || "未绑定"
                                verticalAlignment: Text.AlignVCenter
                                color: "#46556a"
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }

                            Item {
                                width: 120
                                height: parent.height
                                ComboBox {
                                    id: adminUserRoleCombo
                                    width: 106
                                    height: 34
                                    anchors.verticalCenter: parent.verticalCenter
                                    model: ["admin", "operator", "viewer"]
                                    currentIndex: app.adminRoleIndex(adminUserRow.modelData.role)
                                    onActivated: {
                                        if (currentText !== adminUserRow.modelData.role)
                                            client.updateAdminUserRole(adminUserRow.modelData.username, currentText)
                                    }
                                    background: Rectangle {
                                        radius: 7
                                        color: "#ffffff"
                                        border.color: "#E2E8F0"
                                    }
                                    contentItem: Text {
                                        text: app.roleLabel(adminUserRoleCombo.currentText)
                                        color: "#0F172A"
                                        font.pixelSize: 12
                                        verticalAlignment: Text.AlignVCenter
                                        leftPadding: 10
                                        elide: Text.ElideRight
                                    }
                                }
                            }

                            Item {
                                width: 100
                                height: parent.height
                                StatusPill {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: adminUserRow.modelData.is_active ? "启用" : "禁用"
                                    toneColor: adminUserRow.modelData.is_active ? "#16a163" : "#c43e3e"
                                }
                            }

                            Text {
                                width: 70
                                height: parent.height
                                text: String(adminUserRow.modelData.project_count || 0)
                                verticalAlignment: Text.AlignVCenter
                                color: "#46556a"
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }

                            Text {
                                width: 90
                                height: parent.height
                                text: String(adminUserRow.modelData.active_refresh_tokens || 0)
                                verticalAlignment: Text.AlignVCenter
                                color: "#46556a"
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }

                            Text {
                                width: 160
                                height: parent.height
                                text: adminUserRow.modelData.created_at || "-"
                                verticalAlignment: Text.AlignVCenter
                                color: "#46556a"
                                font.pixelSize: 13
                                elide: Text.ElideRight
                            }

                            Item {
                                width: 310
                                height: parent.height
                                Row {
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 8
                                    AppButton {
                                        text: "密码"
                                        compact: true
                                        subtle: true
                                        onClicked: {
                                            app.openAdminResetPasswordDialog(adminUserRow.modelData.username)
                                        }
                                    }
                                    AppButton {
                                        text: adminUserRow.modelData.is_active ? "禁用" : "启用"
                                        compact: true
                                        subtle: true
                                        danger: adminUserRow.modelData.is_active
                                        enabled: !adminUserRow.isSelf || !adminUserRow.modelData.is_active
                                        onClicked: {
                                            if (adminUserRow.modelData.is_active)
                                                app.openConfirm("adminDisableUser", 0, "确认禁用用户", "确定要禁用用户“" + adminUserRow.modelData.username + "”吗？该用户的 refresh token 会被撤销。", adminUserRow.modelData.username)
                                            else
                                                app.openConfirm("adminEnableUser", 0, "确认启用用户", "确定要重新启用用户“" + adminUserRow.modelData.username + "”吗？", adminUserRow.modelData.username)
                                        }
                                    }
                                    AppButton {
                                        text: "撤销令牌"
                                        compact: true
                                        subtle: true
                                        enabled: (adminUserRow.modelData.active_refresh_tokens || 0) > 0
                                        onClicked: app.openConfirm("adminRevokeTokens", 0, "确认撤销令牌", "确定要撤销用户“" + adminUserRow.modelData.username + "”当前所有 refresh token 吗？", adminUserRow.modelData.username)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                visible: app.currentPage === "audit"
                Layout.fillWidth: true
                spacing: 14

                PageTitle {
                    title: "审计记录"
                    subtitle: "追踪项目内的重要操作、资源和用户行为。"
                }

                EmptyState {
                    visible: client.auditList.length === 0
                    title: "暂无审计记录"
                    subtitle: "项目成员操作媒体、检索或资源后会在这里留下记录。"
                }

                TableHeader {
                    visible: client.auditList.length > 0
                    columns: ["时间", "用户", "动作", "资源", "详情"]
                    widths: [170, 120, 190, 120, 360]
                }

                Repeater {
                    model: client.auditList
                    delegate: DataRow {
                        required property var modelData
                        values: [
                            modelData.created_at,
                            modelData.username,
                            modelData.action,
                            modelData.resource_type + "#" + modelData.resource_id,
                            modelData.detail
                        ]
                        widths: [170, 120, 190, 120, 360]
                    }
                }
            }
        }
    }

    FileDialog {
        id: touristEntryDialog
        title: "选择入园登记照片"
        nameFilters: ["Image files (*.jpg *.jpeg *.png *.webp *.bmp)"]
        onAccepted: client.registerTourist(selectedFile, touristTicketField.text, touristNameField.text, touristContactField.text)
    }
}
