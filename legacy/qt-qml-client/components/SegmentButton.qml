pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Button {
    id: segmentRoot
    property bool selected: false
    Layout.fillWidth: true
    implicitHeight: 34
    hoverEnabled: true
    focusPolicy: Qt.NoFocus
    contentItem: Text {
        text: segmentRoot.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        color: segmentRoot.selected ? "white" : "#46556a"
        font.pixelSize: 12
        font.bold: segmentRoot.selected
    }
    background: Rectangle {
        radius: 7
        color: segmentRoot.selected ? "#4F46E5" : (segmentRoot.hovered ? "#eef4fb" : "#f8fafc")
        border.color: segmentRoot.selected ? "#4F46E5" : "#E2E8F0"
    }
}
