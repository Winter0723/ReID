pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Effects

Rectangle {
    id: launcherRoot
    required property var app
    color: "#F8FAFC"

    // Background modern gradient elements
    Rectangle {
        width: 800
        height: 800
        radius: 400
        color: "#1a5a98dc" // light blue
        x: -200
        y: -300
        layer.enabled: true
        layer.effect: MultiEffect { blurEnabled: true; blurMax: 80; blur: 1.0 }
    }

    Rectangle {
        width: 600
        height: 600
        radius: 300
        color: "#1a8b5cf6" // purple
        x: parent.width - 400
        y: parent.height - 400
        layer.enabled: true
        layer.effect: MultiEffect { blurEnabled: true; blurMax: 80; blur: 1.0 }
    }

    ColumnLayout {
        anchors.centerIn: parent
        spacing: 40

        ColumnLayout {
            Layout.alignment: Qt.AlignHCenter
            spacing: 12
            Text {
                text: "欢迎使用 MyReID 智能视觉系统"
                font.pixelSize: 32
                font.bold: true
                color: "#0F172A"
                Layout.alignment: Qt.AlignHCenter
            }
            Text {
                text: "请选择您要进入的工作模式"
                font.pixelSize: 16
                color: "#6b778a"
                Layout.alignment: Qt.AlignHCenter
            }
        }

        RowLayout {
            spacing: 40
            Layout.alignment: Qt.AlignHCenter

            // ReID Card
            Rectangle {
                width: 320
                height: 420
                radius: 16
                color: reidHover.hovered ? "#ffffff" : "#fbfdfd"
                border.color: reidHover.hovered ? "#818CF8" : "#E2E8F0"
                border.width: reidHover.hovered ? 2 : 1
                clip: true

                Behavior on border.color { ColorAnimation { duration: 200 } }
                
                HoverHandler { id: reidHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: app.appMode = "reid" }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 30
                    spacing: 20

                    Rectangle {
                        Layout.preferredWidth: 80
                        Layout.preferredHeight: 80
                        radius: 40
                        color: "#EEF2FF"
                        Text {
                            anchors.centerIn: parent
                            text: "🔍"
                            font.pixelSize: 40
                        }
                    }

                    Text {
                        text: "基础分析系统"
                        font.pixelSize: 24
                        font.bold: true
                        color: "#0F172A"
                    }

                    Text {
                        text: "适用于开发者与专业研究员。\n\n包含完整的向量数据库管理、媒体接入、以文搜图、以图搜图以及模型状态调试功能。"
                        font.pixelSize: 15
                        color: "#64748B"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        verticalAlignment: Text.AlignTop
                    }

                    AppButton {
                        text: "进入基础系统"
                        highlighted: true
                        Layout.fillWidth: true
                        onClicked: app.appMode = "reid"
                    }
                }
            }

            // Scenic Card
            Rectangle {
                width: 320
                height: 420
                radius: 16
                color: scenicHover.hovered ? "#ffffff" : "#fbfdfd"
                border.color: scenicHover.hovered ? "#8b5cf6" : "#E2E8F0"
                border.width: scenicHover.hovered ? 2 : 1
                clip: true

                Behavior on border.color { ColorAnimation { duration: 200 } }

                HoverHandler { id: scenicHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: app.appMode = "scenic" }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 30
                    spacing: 20

                    Rectangle {
                        Layout.preferredWidth: 80
                        Layout.preferredHeight: 80
                        radius: 40
                        color: "#f5f3ff"
                        Text {
                            anchors.centerIn: parent
                            text: "🎬"
                            font.pixelSize: 40
                        }
                    }

                    Text {
                        text: "景区运营平台"
                        font.pixelSize: 24
                        font.bold: true
                        color: "#0F172A"
                    }

                    Text {
                        text: "适用于景区工作人员。\n\n提供游客入园登记、跨摄像头轨迹追踪匹配、自动 VLOG 视频剪辑以及订单交付的一体化工作流。"
                        font.pixelSize: 15
                        color: "#64748B"
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        verticalAlignment: Text.AlignTop
                    }

                    AppButton {
                        text: "进入景区平台"
                        highlighted: true
                        Layout.fillWidth: true
                        onClicked: app.appMode = "scenic"
                        background: Rectangle {
                            color: scenicHover.hovered ? "#7c3aed" : "#8b5cf6"
                            radius: 6
                        }
                    }
                }
            }
        }
    }
}
