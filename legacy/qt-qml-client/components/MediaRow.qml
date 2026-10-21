pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window
import QtMultimedia
import "."

Rectangle {
    required property var app
    id: mediaRowRoot
    required property var media
    property bool expanded: false
    property var targets: media && media.targets ? media.targets : []
    readonly property bool isVideo: Boolean(media && media.media_type === "video")
    readonly property bool hasCameraRecording: Boolean(media && media.media_type === "camera" && String(media.object_url || "").length > 0 && String(media.content_type || "").indexOf("video/") === 0)
    readonly property bool isPlayableVideo: isVideo || hasCameraRecording
    readonly property bool isExpandable: media && (media.media_type === "video" || media.media_type === "camera")
    readonly property string mediaObjectUrl: media && media.object_url ? media.object_url : ""
    readonly property string mediaPreviewUrl: mediaObjectUrl.length > 0 ? mediaObjectUrl : (media && media.thumbnail_url ? media.thumbnail_url : "")
    signal deleteRequested(int mediaId)

    Layout.fillWidth: true
    Layout.preferredHeight: mediaContent.implicitHeight + 24
    radius: 8
    color: mediaRowHover.hovered ? "#FFFFFF" : "#ffffff"
    border.color: "#e1e7ef"

    HoverHandler { id: mediaRowHover }

    ColumnLayout {
        id: mediaContent
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
        spacing: 10

        Row {
            Layout.fillWidth: true
            Layout.preferredHeight: 66

            Item {
                width: 82
                height: parent.height
                Rectangle {
                    width: 68
                    height: 52
                    anchors.verticalCenter: parent.verticalCenter
                    radius: 7
                    clip: true
                    color: "#F1F5F9"
                    border.color: "#d7e0eb"
                    Image {
                        id: mediaThumb
                        anchors.fill: parent
                        source: mediaRowRoot.app.absoluteUrl(mediaRowRoot.media.thumbnail_url || "")
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                    }
                    Text {
                        anchors.centerIn: parent
                        visible: !mediaThumb.source || mediaThumb.status === Image.Error
                        text: mediaRowRoot.media.media_type === "camera" ? "相机" : (mediaRowRoot.isVideo ? "视频" : "图片")
                        color: "#657386"
                        font.pixelSize: 12
                        font.bold: true
                    }
                }
            }

            Item {
                width: 260
                height: parent.height
                Column {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 4
                    Text {
                        width: parent.width - 12
                        text: mediaRowRoot.media.filename || "-"
                        color: "#0F172A"
                        font.pixelSize: 13
                        font.bold: true
                        elide: Text.ElideRight
                    }
                    Text {
                        width: parent.width - 12
                        text: mediaRowRoot.media.content_type || ""
                        color: "#94A3B8"
                        font.pixelSize: 11
                        elide: Text.ElideRight
                    }
                }
            }

            Text {
                width: 64
                height: parent.height
                text: mediaRowRoot.app.mediaTypeLabel(mediaRowRoot.media.media_type)
                verticalAlignment: Text.AlignVCenter
                color: "#46556a"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Text {
                width: 86
                height: parent.height
                text: mediaRowRoot.app.formatBytes(mediaRowRoot.media.size_bytes)
                verticalAlignment: Text.AlignVCenter
                color: "#46556a"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Text {
                width: 92
                height: parent.height
                text: mediaRowRoot.app.statusLabel(mediaRowRoot.media.status)
                verticalAlignment: Text.AlignVCenter
                color: mediaRowRoot.media.status === "failed" ? "#c43e3e" : "#46556a"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Text {
                width: 70
                height: parent.height
                text: String(mediaRowRoot.media.target_count || 0)
                verticalAlignment: Text.AlignVCenter
                color: "#46556a"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Text {
                width: 140
                height: parent.height
                text: mediaRowRoot.media.created_at || "-"
                verticalAlignment: Text.AlignVCenter
                color: "#46556a"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Item {
                width: 248
                height: parent.height
                Row {
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 6
                    AppButton {
                        text: "预览"
                        compact: true
                        subtle: true
                        enabled: mediaRowRoot.mediaPreviewUrl.length > 0
                        onClicked: mediaRowRoot.app.previewMedia(mediaRowRoot.isPlayableVideo ? "video" : "image", mediaRowRoot.media.filename || "媒体预览", mediaRowRoot.mediaPreviewUrl, 0, mediaRowRoot.mediaObjectUrl || mediaRowRoot.mediaPreviewUrl, mediaRowRoot.media.filename || "", mediaRowRoot.isPlayableVideo ? (mediaRowRoot.media.thumbnail_url || "") : "")
                    }
                    AppButton {
                        text: mediaRowRoot.hasCameraRecording ? "下载录像" : "下载"
                        compact: true
                        subtle: true
                        enabled: mediaRowRoot.mediaPreviewUrl.length > 0
                        onClicked: mediaRowRoot.app.requestDownload(mediaRowRoot.mediaObjectUrl || mediaRowRoot.mediaPreviewUrl, mediaRowRoot.media.filename || "")
                    }
                    AppButton {
                        visible: mediaRowRoot.isExpandable
                        enabled: mediaRowRoot.targets.length > 0
                        text: mediaRowRoot.expanded ? "收起" : (mediaRowRoot.targets.length > 0 ? "展开" : "暂无")
                        compact: true
                        subtle: true
                        onClicked: mediaRowRoot.expanded = !mediaRowRoot.expanded
                    }
                    AppButton {
                        text: "删除"
                        compact: true
                        subtle: true
                        danger: true
                        onClicked: mediaRowRoot.deleteRequested(mediaRowRoot.media.id)
                    }
                }
            }
        }

        Rectangle {
            id: targetsPanel
            visible: mediaRowRoot.isExpandable && mediaRowRoot.expanded
            Layout.fillWidth: true
            Layout.preferredHeight: visible ? targetContent.implicitHeight + 20 : 0
            radius: 7
            color: "#f8fafc"
            border.color: "#E2E8F0"

            ColumnLayout {
                id: targetContent
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 10
                spacing: 10

                Text {
                    text: "识别图像 " + mediaRowRoot.targets.length
                    color: "#46556a"
                    font.pixelSize: 12
                    font.bold: true
                    Layout.fillWidth: true
                }

                Flow {
                    Layout.fillWidth: true
                    spacing: 10
                    Repeater {
                        model: mediaRowRoot.targets
                        delegate: Rectangle {
                            required property var modelData
                            width: 128
                            height: 166
                            radius: 7
                            color: "#ffffff"
                            border.color: "#E2E8F0"
                            clip: true

                            Image {
                                id: targetImage
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                height: 94
                                source: mediaRowRoot.app.absoluteUrl(modelData.thumbnail_url || "")
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                            }
                            Text {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: targetImage.bottom
                                anchors.margins: 7
                                text: modelData.timestamp || ("#" + modelData.id)
                                color: "#657386"
                                font.pixelSize: 11
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                            Row {
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.bottom: parent.bottom
                                anchors.margins: 7
                                spacing: 6
                                AppButton {
                                    width: 54
                                    text: "预览"
                                    compact: true
                                    subtle: true
                                    onClicked: {
                                        var source = mediaRowRoot.isPlayableVideo && mediaRowRoot.mediaObjectUrl.length > 0 ? mediaRowRoot.mediaObjectUrl : (modelData.thumbnail_url || "")
                                        var type = mediaRowRoot.isPlayableVideo && mediaRowRoot.mediaObjectUrl.length > 0 ? "video" : "image"
                                        var seekSeconds = modelData.video_seek_seconds !== undefined && modelData.video_seek_seconds !== null ? Number(modelData.video_seek_seconds) : mediaRowRoot.app.timestampToSeconds(modelData.timestamp || 0)
                                        mediaRowRoot.app.previewMedia(type, mediaRowRoot.media.filename || "识别图像", source, seekSeconds, source, mediaRowRoot.media.filename || "", modelData.thumbnail_url || "")
                                    }
                                }
                                AppButton {
                                    width: 54
                                    text: "下载"
                                    compact: true
                                    subtle: true
                                    onClicked: mediaRowRoot.app.requestDownload(modelData.thumbnail_url || "", "myreid_target_" + (modelData.id || "frame") + ".jpg")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
