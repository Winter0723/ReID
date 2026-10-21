pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

RowLayout {
    id: pageTitleRoot
    property string title: ""
    property string subtitle: ""
    property string actionText: ""
    property string secondaryActionText: ""
    property bool actionEnabled: true
    property bool secondaryActionEnabled: true
    signal actionTriggered()
    signal secondaryActionTriggered()

    Layout.fillWidth: true
    spacing: 12
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 4
        Text {
            text: pageTitleRoot.title
            color: "#0F172A"
            font.pixelSize: 22
            font.bold: true
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
        Text {
            text: pageTitleRoot.subtitle
            color: "#657386"
            font.pixelSize: 13
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }
    AppButton {
        visible: pageTitleRoot.secondaryActionText.length > 0
        text: pageTitleRoot.secondaryActionText
        enabled: pageTitleRoot.secondaryActionEnabled
        onClicked: pageTitleRoot.secondaryActionTriggered()
    }
    AppButton {
        visible: pageTitleRoot.actionText.length > 0
        text: pageTitleRoot.actionText
        enabled: pageTitleRoot.actionEnabled
        highlighted: true
        onClicked: pageTitleRoot.actionTriggered()
    }
}
