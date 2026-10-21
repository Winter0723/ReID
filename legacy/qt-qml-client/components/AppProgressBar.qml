pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

ProgressBar {
    id: progressRoot
    implicitHeight: 8
    background: Rectangle {
        radius: 4
        color: "#e6ecf4"
    }
    contentItem: Item {
        Rectangle {
            width: progressRoot.visualPosition * parent.width
            height: parent.height
            radius: 4
            color: "#4F46E5"
        }
    }
}
