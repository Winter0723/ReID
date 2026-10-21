pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    id: dataRowRoot
    property var values: []
    property var widths: []
    property int actionIndex: -1
    property bool dangerAction: false
    signal actionTriggered()
    Layout.fillWidth: true
    height: 50
    radius: 7
    color: dataRowHover.hovered ? "#FFFFFF" : "#ffffff"
    border.color: "#e1e7ef"

    HoverHandler { id: dataRowHover }

    Row {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        Repeater {
            model: dataRowRoot.values ? dataRowRoot.values.length : 0
            delegate: Item {
                required property int index
                width: dataRowRoot.widths && dataRowRoot.widths.length > index ? dataRowRoot.widths[index] : 120
                height: parent.height
                property string cellText: dataRowRoot.values && dataRowRoot.values.length > index && dataRowRoot.values[index] !== undefined && dataRowRoot.values[index] !== null ? String(dataRowRoot.values[index]) : ""

                Text {
                    anchors.fill: parent
                    visible: index !== dataRowRoot.actionIndex
                    text: parent.cellText
                    verticalAlignment: Text.AlignVCenter
                    color: "#46556a"
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }

                AppButton {
                    visible: index === dataRowRoot.actionIndex
                    text: parent.cellText
                    compact: true
                    danger: dataRowRoot.dangerAction
                    subtle: true
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: dataRowRoot.actionTriggered()
                }
            }
        }
    }
}
