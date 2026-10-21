pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

TextField {
    id: inputRoot
    implicitHeight: 40
    font.pixelSize: 13
    color: "#0F172A"
    placeholderTextColor: "#94A3B8"
    selectedTextColor: "white"
    selectionColor: "#4F46E5"
    leftPadding: 12
    rightPadding: 12
    background: Rectangle {
        radius: 7
        color: inputRoot.enabled ? "#ffffff" : "#F1F5F9"
        border.color: inputRoot.activeFocus ? "#4F46E5" : "#E2E8F0"
        border.width: inputRoot.activeFocus ? 1.4 : 1
    }
}
