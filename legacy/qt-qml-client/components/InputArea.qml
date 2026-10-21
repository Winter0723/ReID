pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

TextArea {
    id: areaRoot
    font.pixelSize: 13
    color: "#0F172A"
    placeholderTextColor: "#94A3B8"
    selectedTextColor: "white"
    selectionColor: "#4F46E5"
    leftPadding: 12
    rightPadding: 12
    topPadding: 10
    bottomPadding: 10
    background: Rectangle {
        radius: 7
        color: "#ffffff"
        border.color: areaRoot.activeFocus ? "#4F46E5" : "#E2E8F0"
        border.width: areaRoot.activeFocus ? 1.4 : 1
    }
}
