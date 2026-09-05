import QtQuick
import QtQuick.Layouts

ColumnLayout {
    property string title: ""
    property string subtitle: ""
    spacing: 5
    Text { text: title; color: "#e5edf5"; font.pixelSize: 18; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    Text { text: subtitle; visible: subtitle.length > 0; color: "#90a4b8"; font.pixelSize: 12; lineHeight: 1.35; Layout.fillWidth: true; wrapMode: Text.WordWrap }
}
