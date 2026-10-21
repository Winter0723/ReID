pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Button {
    id: buttonRoot
    property bool subtle: false
    property bool danger: false
    property bool compact: false

    implicitHeight: compact ? 32 : 38
    implicitWidth: buttonLabel.implicitWidth + (compact ? 22 : 32)
    hoverEnabled: true
    focusPolicy: Qt.NoFocus
    leftPadding: compact ? 10 : 14
    rightPadding: compact ? 10 : 14

    contentItem: Text {
        id: buttonLabel
        text: buttonRoot.text
        font.pixelSize: buttonRoot.compact ? 12 : 13
        font.bold: buttonRoot.highlighted
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        color: !buttonRoot.enabled ? "#99a3b3" : (buttonRoot.highlighted ? "white" : (buttonRoot.danger ? "#c43e3e" : "#0F172A"))
    }

    background: Rectangle {
        radius: 7
        border.width: 1
        border.color: !buttonRoot.enabled ? "#E2E8F0" : (buttonRoot.danger ? "#f1c7c7" : (buttonRoot.highlighted ? "#4F46E5" : "#E2E8F0"))
        color: !buttonRoot.enabled ? "#F1F5F9"
               : buttonRoot.down ? (buttonRoot.danger ? "#b83232" : (buttonRoot.highlighted ? "#4338CA" : "#E2E8F0"))
               : buttonRoot.danger && buttonRoot.highlighted ? (buttonRoot.hovered ? "#b83232" : "#c43e3e")
               : buttonRoot.highlighted ? (buttonRoot.hovered ? "#4F46E5" : "#4F46E5")
               : buttonRoot.subtle ? (buttonRoot.hovered ? "#F1F5F9" : "#f8fafc")
               : buttonRoot.hovered ? "#F8FAFC" : "#ffffff"
    }
}
