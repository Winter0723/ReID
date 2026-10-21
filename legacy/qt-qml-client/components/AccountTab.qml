pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Button {
    id: accountTabRoot
    property bool selected: false
    property bool danger: false
    Layout.fillWidth: true
    implicitHeight: 38
    hoverEnabled: true
    focusPolicy: Qt.NoFocus
    leftPadding: 10
    rightPadding: 10
    contentItem: Text {
        text: accountTabRoot.text
        color: accountTabRoot.danger ? "#c43e3e" : (accountTabRoot.selected ? "#0F172A" : "#64748B")
        font.pixelSize: 13
        font.bold: accountTabRoot.selected
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 7
        color: accountTabRoot.selected ? "#EEF2FF" : (accountTabRoot.hovered ? "#f3f7fb" : "transparent")
        border.color: accountTabRoot.selected ? "#9ac4ff" : "transparent"
    }
}
