pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Dialog {
    id: appDialogRoot
    anchors.centerIn: parent
    modal: true
    padding: 18
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle {
        color: "#ffffff"
        radius: 10
        border.color: "#E2E8F0"
    }
    header: Rectangle {
        implicitHeight: 52
        color: "#ffffff"
        radius: 10
        border.color: "#E2E8F0"
        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: "#edf1f6"
        }
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 18
            anchors.rightMargin: 12
            spacing: 10
            Text {
                text: appDialogRoot.title
                color: "#0F172A"
                font.pixelSize: 15
                font.bold: true
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            Button {
                id: appDialogCloseButton
                implicitWidth: 30
                implicitHeight: 30
                hoverEnabled: true
                focusPolicy: Qt.NoFocus
                contentItem: Text {
                    text: "×"
                    color: "#657386"
                    font.pixelSize: 18
                    horizontalAlignment: Text.AlignHCenter
                    verticalAlignment: Text.AlignVCenter
                }
                background: Rectangle {
                    radius: 7
                    color: appDialogCloseButton.hovered ? "#F1F5F9" : "transparent"
                    border.color: "transparent"
                }
                onClicked: appDialogRoot.close()
            }
        }
    }
}
