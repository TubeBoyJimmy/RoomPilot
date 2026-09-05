import QtQuick
import QtQuick.Layouts

Item {
    id: control
    property string symbol: "∿"
    property string title: "尚無資料"
    property string subtitle: ""
    implicitHeight: 210
    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - 40, 420)
        spacing: 13
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: 54; height: 54; radius: 16
            color: "#21384a"
            Text { anchors.centerIn: parent; text: control.symbol; color: "#83dbc5"; font.family: "Segoe UI Symbol"; font.pixelSize: 28 }
        }
        Text { Layout.fillWidth: true; text: control.title; color: "#dce6f1"; font.pixelSize: 17; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap }
        Text { Layout.fillWidth: true; text: control.subtitle; color: "#90a4b8"; font.pixelSize: 13; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; lineHeight: 1.35 }
    }
}
