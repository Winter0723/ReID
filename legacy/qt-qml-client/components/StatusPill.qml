pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: pillRoot
    property string text: ""
    property color toneColor: "#4F46E5"
    implicitWidth: pillText.implicitWidth + 20
    implicitHeight: 26
    radius: 13
    color: Qt.rgba(toneColor.r, toneColor.g, toneColor.b, 0.12)
    border.color: Qt.rgba(toneColor.r, toneColor.g, toneColor.b, 0.28)
    Text {
        id: pillText
        anchors.centerIn: parent
        text: pillRoot.text
        color: pillRoot.toneColor
        font.pixelSize: 12
        font.bold: true
    }
}
