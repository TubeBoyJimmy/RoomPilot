import QtQuick
import QtQuick.Controls

TextField {
    id: control
    implicitHeight: 40
    color: "#e5edf5"
    font.pixelSize: 13
    selectByMouse: true
    selectionColor: "#3e7d74"
    selectedTextColor: "#ffffff"
    placeholderTextColor: "#6f8297"
    leftPadding: 12
    rightPadding: 12
    background: Rectangle {
        radius: 7
        color: "#0e1a29"
        border.color: control.activeFocus ? "#83dbc5" : "#304157"
        border.width: 1
    }
}
