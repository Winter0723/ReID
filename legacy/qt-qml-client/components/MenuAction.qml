pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Button {
    id: menuActionRoot
    property bool danger: false
    Layout.fillWidth: true
    implicitHeight: 34
    hoverEnabled: true
    focusPolicy: Qt.NoFocus
    leftPadding: 10
    rightPadding: 10
    contentItem: Text {
        text: menuActionRoot.text
        color: menuActionRoot.danger ? "#c43e3e" : "#0F172A"
        font.pixelSize: 13
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 7
        color: menuActionRoot.hovered ? (menuActionRoot.danger ? "#fff1f1" : "#F8FAFC") : "transparent"
        border.color: "transparent"
    }
}
