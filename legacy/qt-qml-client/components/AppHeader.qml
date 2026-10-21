pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: headerRoot
    required property var app
    required property var client
    property alias statusMessage: statusText.text
    property alias progressVisible: progressBar.visible
    property alias progressValue: progressBar.value
    Layout.fillWidth: true
    Layout.preferredHeight: 88
    color: "#FFFFFF"
    border.color: "#E2E8F0"

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        spacing: 14

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            Text {
                text: client.currentProjectId > 0 ? client.currentProjectName : "请选择或创建项目"
                color: "#0F172A"
                font.pixelSize: 22
                font.bold: true
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                StatusPill {
                    text: client.isLoggedIn ? "已连接" : "未登录"
                    toneColor: client.isLoggedIn ? "#16a163" : "#d38b17"
                }
                Text {
                    id: statusText
                    text: client.isLoggedIn ? "系统已连接" : "请登录后使用系统接口"
                    color: app.isSystemReady ? "#15803d" : "#8a5a10"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }

        AppProgressBar {
            id: progressBar
            Layout.preferredWidth: 170
            visible: false
            value: 0
        }

        AppButton {
            text: "上传图片"
            enabled: client.currentProjectId > 0
            highlighted: client.currentProjectId > 0 && !app.isSystemReady
            onClicked: {
                app.currentPage = "media"
                app.isAppendMode = true
                app.openGalleryDialog()
            }
        }

        AppButton {
            text: "上传视频"
            enabled: client.currentProjectId > 0
            onClicked: {
                app.currentPage = "media"
                app.isAppendMode = true
                app.openVideoDialog()
            }
        }

        AppButton {
            text: client.cameraRunning ? "暂停入库" : "实时入库"
            visible: app.isCameraProject()
            enabled: client.currentProjectId > 0
            highlighted: app.isCameraProject() && !client.cameraRunning
            onClicked: {
                app.currentPage = "camera"
                if (client.cameraRunning)
                    client.stopCamera()
                else
                    client.startCamera(client.cameraSource || "0")
            }
        }

        AppButton {
            text: "去检索"
            enabled: client.currentProjectId > 0
            highlighted: app.isSystemReady
            onClicked: app.currentPage = "search"
        }

        AppButton {
            text: "刷新"
            enabled: client.isLoggedIn
            subtle: true
            onClicked: {
                client.fetchProjects()
                client.refreshWorkspace()
            }
        }

        AppButton {
            id: userButton
            text: client.isLoggedIn ? client.currentUser : "登录"
            Layout.preferredWidth: client.isLoggedIn ? 132 : 72
            highlighted: !client.isLoggedIn
            subtle: client.isLoggedIn
            onClicked: {
                if (client.isLoggedIn)
                    app.openUserMenu()
                else
                    app.openAuthDialog()
            }
            TapHandler {
                acceptedButtons: Qt.RightButton
                onTapped: {
                    if (client.isLoggedIn)
                        app.openUserMenu()
                }
            }
        }
    }
}
