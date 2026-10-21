pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: emptyRoot
    property string title: ""
    property string subtitle: ""
    Layout.fillWidth: true
    Layout.preferredHeight: 132
    radius: 8
    color: "#ffffff"
    border.color: "#E2E8F0"
    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 48, 520)
        spacing: 8
        Text {
            text: emptyRoot.title
            color: "#0F172A"
            font.pixelSize: 16
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        Text {
            text: emptyRoot.subtitle
            color: "#657386"
            font.pixelSize: 13
            horizontalAlignment: Text.AlignHCenter
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
    }
}
