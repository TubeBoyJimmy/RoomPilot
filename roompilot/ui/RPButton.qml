import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string variant: "secondary"
    property bool compact: false
    implicitHeight: compact ? 34 : 42
    implicitWidth: Math.max(76, contentItem.implicitWidth + 28)
    leftPadding: 14
    rightPadding: 14
    font.pixelSize: 13
    font.weight: Font.DemiBold
    hoverEnabled: true
    contentItem: Text {
        text: control.text
        font: control.font
        color: !control.enabled ? "#58697c" : control.variant === "primary" ? "#082825" : control.variant === "danger" ? "#ffb4a7" : "#dce6f1"
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: Rectangle {
        radius: 8
        color: !control.enabled ? "#172333" : control.variant === "primary" ? (control.down ? "#5fbfab" : control.hovered ? "#a5ecd9" : "#83dbc5") : control.variant === "ghost" ? (control.hovered ? "#223247" : "transparent") : (control.down ? "#2a3c51" : control.hovered ? "#28394d" : "#1b2b3f")
        border.width: control.variant === "primary" || control.variant === "ghost" ? 0 : 1
        border.color: control.activeFocus ? "#83dbc5" : "#344356"
    }
}
