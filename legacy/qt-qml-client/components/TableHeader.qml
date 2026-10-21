pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: tableHeaderRoot
    property var columns: []
    property var widths: []
    Layout.fillWidth: true
    height: 42
    color: "#eaf0f7"
    radius: 7
    border.color: "#dbe3ee"
    Row {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        Repeater {
            model: tableHeaderRoot.columns.length
            delegate: Text {
                required property int index
                width: tableHeaderRoot.widths && tableHeaderRoot.widths.length > index ? tableHeaderRoot.widths[index] : 120
                height: parent.height
                text: tableHeaderRoot.columns && tableHeaderRoot.columns.length > index ? tableHeaderRoot.columns[index] : ""
                verticalAlignment: Text.AlignVCenter
                font.bold: true
                font.pixelSize: 12
                color: "#46556a"
                elide: Text.ElideRight
            }
        }
    }
}
