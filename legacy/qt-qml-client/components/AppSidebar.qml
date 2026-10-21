pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    required property var app
    required property var client
    Layout.preferredWidth: app.sidebarWidth
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
                    text: "行"
                    color: "white"
                    font.pixelSize: 20
                    font.bold: true
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    text: "行人重识别系统"
                    color: "white"
                    font.pixelSize: 18
                    font.bold: true
                    Layout.fillWidth: true
                }
                Text {
                    text: "行人重识别系统"
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
                    text: client.currentProjectId > 0 ? client.currentProjectName : "未选择项目"
                    color: "#f8fafc"
                    font.pixelSize: 14
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Text {
                    text: client.isLoggedIn ? "当前账号 " + client.currentUser : "登录后启用系统接口"
                    color: "#93a4bb"
                    font.pixelSize: 12
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                StatusPill {
                    text: app.isSystemReady ? "索引就绪" : "待处理"
                    toneColor: app.isSystemReady ? "#16a163" : "#d38b17"
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
                {"key": "projects", "label": "项目列表"},
                {"key": "detail", "label": "项目详情"},
                {"key": "media", "label": "媒体管理"},
                {"key": "camera", "label": "实时相机"},
                {"key": "jobs", "label": "任务进度"},
                {"key": "search", "label": "检索页"},
                {"key": "history", "label": "历史页"},
                {"key": "users", "label": "用户管理"},
                {"key": "audit", "label": "审计页"}
            ]

            delegate: SidebarItem {
                required property var modelData
                Layout.fillWidth: true
                visible: modelData.key !== "users" || client.currentRole === "admin"
                label: modelData.label
                iconText: app.navIcon(modelData.key)
                selected: app.currentPage === modelData.key
                enabled: modelData.key === "projects"
                         || (modelData.key === "users" ? client.currentRole === "admin" : (modelData.key === "camera" ? client.currentProjectId > 0 && app.isCameraProject() : client.currentProjectId > 0))
                onActivated: {
                    app.currentPage = modelData.key
                    if (modelData.key === "users")
                        client.fetchAdminUsers()
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
            Layout.preferredHeight: 78
            radius: 8
            color: "#0b1220"
            border.color: "#223047"
            Text {
                anchors.fill: parent
                anchors.margins: 12
                text: client.enterpriseStatus
                color: "#94a3b8"
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
}
