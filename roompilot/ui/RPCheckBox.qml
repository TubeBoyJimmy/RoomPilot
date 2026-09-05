import QtQuick
import QtQuick.Controls

CheckBox {
    id: control
    font.pixelSize: 13
    spacing: 10
    leftPadding: 0
    implicitHeight: Math.max(34, contentItem.implicitHeight + 12)
    indicator: Rectangle {
        implicitWidth: 19
        implicitHeight: 19
        x: 0
        y: (control.height - height) / 2
        radius: 5
        color: control.checked ? "#83dbc5" : "#0f1c2b"
        border.color: control.checked ? "#83dbc5" : "#52687d"
        Text { anchors.centerIn: parent; text: "✓"; color: "#092825"; visible: control.checked; font.family: "Segoe UI Symbol"; font.pixelSize: 14; font.bold: true }
    }
    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? "#d0dce8" : "#718598"
        wrapMode: Text.WordWrap
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
    }
}
