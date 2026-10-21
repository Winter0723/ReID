pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

RowLayout {
    id: profileRowRoot
    property string label: ""
    property string value: ""
    Layout.fillWidth: true
    spacing: 12
    Text {
        text: profileRowRoot.label
        color: "#657386"
        font.pixelSize: 13
        Layout.preferredWidth: 82
        elide: Text.ElideRight
    }
    Text {
        text: profileRowRoot.value
        color: "#0F172A"
        font.pixelSize: 13
        Layout.fillWidth: true
        elide: Text.ElideRight
    }
}
