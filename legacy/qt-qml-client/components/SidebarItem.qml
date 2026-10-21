pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: sidebarRoot
    property string label: ""
    property string iconText: ""
    property bool selected: false
    signal activated()

    height: 42
    radius: 8
    color: !enabled ? "transparent" : (selected ? "#EEF2FF" : (navHover.hovered ? "#1b2840" : "transparent"))
    border.color: selected ? "#9ac4ff" : "transparent"
    opacity: enabled ? 1 : 0.42

    HoverHandler { id: navHover }
    TapHandler {
        enabled: sidebarRoot.enabled
        onTapped: sidebarRoot.activated()
    }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 10
        spacing: 10
        Rectangle {
            Layout.preferredWidth: 24
            Layout.preferredHeight: 24
            radius: 7
            color: sidebarRoot.selected ? "#4F46E5" : "#27354f"
            Text {
                anchors.centerIn: parent
                text: sidebarRoot.iconText
                color: sidebarRoot.selected ? "white" : "#94A3B8"
                font.pixelSize: 12
                font.bold: true
            }
        }
        Text {
            text: sidebarRoot.label
            color: sidebarRoot.selected ? "#0F172A" : "#dbe5f3"
            font.pixelSize: 14
            font.bold: sidebarRoot.selected
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
        Rectangle {
            Layout.preferredWidth: 5
            Layout.preferredHeight: 18
            radius: 3
            color: sidebarRoot.selected ? "#4F46E5" : "transparent"
        }
    }
}
