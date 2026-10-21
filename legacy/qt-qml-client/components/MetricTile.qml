pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: metricRoot
    property string title: ""
    property string value: ""
    property color accentColor: "#4F46E5"
    property bool compact: false
    Layout.fillWidth: !compact
    height: compact ? 84 : 92
    radius: 8
    color: compact ? "#202b3e" : "#ffffff"
    border.color: compact ? "#31405a" : "#E2E8F0"
    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 7
        Rectangle {
            Layout.preferredWidth: 28
            Layout.preferredHeight: 4
            radius: 2
            color: metricRoot.accentColor
        }
        Text {
            text: metricRoot.title
            color: metricRoot.compact ? "#b9c6d8" : "#657386"
            font.pixelSize: 12
        }
        Text {
            text: metricRoot.value
            color: metricRoot.compact ? "white" : "#0F172A"
            font.pixelSize: metricRoot.compact ? 24 : 28
            font.bold: true
        }
    }
}
