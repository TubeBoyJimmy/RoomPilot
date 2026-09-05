import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id:page
    objectName:"historyPage"
    required property var app
    property var s:app.s
    function inspectBaseline(b) {
        var settings = b.settings || {};
        var lines = ["量測條件", "麥克風朝向：" + (settings.mic_orientation || "未提供"),
            "主音量／增益：" + (settings.volume_note || "未提供"),
            "播放路徑：" + (settings.route_note || "未提供"),
            "錄音輸入：" + (settings.input_device || "未提供"),
            "播放輸出：" + (settings.output_device || "未提供"),
            "環境備註：" + (settings.room_note || "未提供"), "", "此版包含的量測"];
        var measurements = b.measurements || [];
        for(var i=0;i<measurements.length;i++) {
            var m=measurements[i], meta=m.metadata || {};
            var status=meta.cal_status || m.cal_status;
            lines.push((i+1) + ". " + m.name + " · " + m.channel + " · " + m.position);
            lines.push("    Mic Cal：" + (status === "loaded" ? (meta.cal_name || m.cal_name || "已載入") : status === "missing" ? "未載入" : "資料不足，無法確認"));
            lines.push("    錄音輸入：" + (meta.input_device || m.input_device || "未提供"));
            lines.push("    播放輸出：" + (meta.output_device || m.output_device || "未提供"));
            lines.push("    取樣率：" + (meta.sample_rate ? app.num(meta.sample_rate/1000,1) + " kHz" : "未提供") + "；掃頻輸出：" + (meta.sweep_level_dbfs !== undefined && meta.sweep_level_dbfs !== null ? app.num(meta.sweep_level_dbfs,1) + " dBFS" : "未提供"));
        }
        var checks=settings.checklist || {};
        var labels={quiet:"關閉冷氣、除濕機與風扇",notifications:"暫停音樂、通知與談話",mic_position:"固定麥克風位置",room_state:"固定門窗家具狀態",processing:"記錄音量與音效處理",channels:"確認左右聲道",rew_io:"REW 輸入輸出",rew_cal:"核對麥克風 Cal",rew_sweep:"設定掃頻",rew_levels:"Check Levels",rew_fixed:"保存量測設定",record_l:"左聲道兩筆",record_r:"右聲道兩筆",record_save:"保存全部量測",record_dsp:"確認 DSP 訊號路徑"};
        var keys=Object.keys(checks);
        if(keys.length) { lines.push("", "量測準備清單快照"); for(var c=0;c<keys.length;c++) lines.push((checks[keys[c]] ? "[完成] " : "[未勾選] ") + (labels[keys[c]] || keys[c])); }
        lines.push("", "品質檢查快照", "使用者確認提醒：" + (b.warnings_acknowledged ? "是" : "否"));
        var quality=b.quality || [];
        for(var j=0;j<quality.length;j++) {
            var q=quality[j];
            lines.push("", "[" + (({pass:"已確認",warning:"需要注意",error:"需要處理",unknown:"資料不足"})[q.level] || "待確認") + "] " + q.title);
            lines.push(q.detail || "");
        }
        snapshotDialog.heading="Baseline v" + b.version + " · 基準快照";
        snapshotDialog.subheading=app.formatTime(b.created_at) + " · 保留建立此版時的量測與條件";
        snapshotDialog.bodyText=lines.join("\n");
        snapshotDialog.open();
    }
    clip:true
    contentWidth:availableWidth
    ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
    ColumnLayout {
        width:page.availableWidth
        spacing:18
        ColumnLayout {
            Layout.fillWidth:true; Layout.leftMargin:30; Layout.rightMargin:30; Layout.bottomMargin:26
            spacing:20
            RPCard {
                Layout.fillWidth:true
                implicitHeight:105
                RowLayout {
                    anchors.fill:parent; anchors.margins:22; spacing:20
                    SectionTitle { title:"帶著整個專案繼續聆聽"; subtitle:"專案包包含量測、環境設定、Baseline、PEQ 版本與驗證紀錄，可在另一台電腦接續。"; Layout.fillWidth:true }
                    RPButton { objectName:"importProjectBundle"; text:"開啟專案包"; enabled:!s.busy; onClicked:bridge.importProject() }
                    RPButton { objectName:"exportProjectBundle"; text:"匯出專案包"; variant:"primary"; enabled:app.hasProject && !s.busy; onClicked:bridge.exportProject() }
                }
            }
            RowLayout {
                Layout.fillWidth:true
                Layout.alignment:Qt.AlignTop
                spacing:19
                RPCard {
                    Layout.fillWidth:true
                    Layout.preferredWidth:460
                    Layout.alignment:Qt.AlignTop
                    implicitHeight:Math.max(350,versionColumn.implicitHeight + 40)
                    ColumnLayout {
                        id:versionColumn
                        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20
                        spacing:15
                        SectionTitle { title:"PEQ 版本"; subtitle:"查看參數與驗證；不需要的方案可移除，之後仍能復原。"; Layout.fillWidth:true }
                        EmptyState { visible:!(s.peqs || []).length; Layout.fillWidth:true; Layout.preferredHeight:230; symbol:"≋"; title:"尚未建立 PEQ 方案"; subtitle:"完成第一輪建議後，每一版都會出現在這裡。" }
                        Repeater {
                            model:s.peqs || []
                            delegate:Rectangle {
                                required property var modelData
                                Layout.fillWidth:true
                                implicitHeight:versionRow.implicitHeight + 28
                                radius:8
                                color:app.selectedPeq.id === modelData.id ? "#203b47" : "#101e2e"
                                border.color:app.selectedPeq.id === modelData.id ? "#4d8b83" : "#2a3f52"
                                RowLayout {
                                    id:versionRow
                                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:14; spacing:12
                                    ColumnLayout {
                                        Layout.fillWidth:true; spacing:7
                                        Text { text:modelData.name || "PEQ 方案"; color:"#d8e8f1"; font.pixelSize:14; font.weight:Font.DemiBold; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:"Baseline v" + (modelData.baseline_version || 1) + " · " + (modelData.variant_count > 1 ? modelData.variant_count + " 種策略 · " + (modelData.has_applied_variant ? "含已套用紀錄" : "尚未套用") : app.statusLabel(modelData.status)); color:"#81b9ac"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:app.formatTime(modelData.created_at); color:"#708da6"; font.pixelSize:10 }
                                    }
                                    RPButton { objectName:"openPeq_" + modelData.id; text:"查看 →"; compact:true; variant:"ghost"; onClicked:{bridge.selectPeq(modelData.id);app.go(2)} }
                                    RPButton { objectName:"deletePeq_" + modelData.id; text:"移除"; compact:true; variant:"danger"; enabled:!s.busy; onClicked:{deleteDialog.peqId=modelData.id;deleteDialog.peqName=modelData.name || "PEQ";deleteDialog.open()} }
                                }
                            }
                        }
                        Rectangle { visible:(s.deleted_peqs || []).length > 0; Layout.fillWidth:true; height:1; color:"#2b4052"; Layout.topMargin:7 }
                        SectionTitle { visible:(s.deleted_peqs || []).length > 0; title:"已移除的 PEQ"; subtitle:"方案資料與補錄關聯仍保留，可重新放回方案清單。"; Layout.fillWidth:true }
                        Repeater {
                            model:s.deleted_peqs || []
                            delegate:Rectangle {
                                required property var modelData
                                Layout.fillWidth:true
                                implicitHeight:deletedRow.implicitHeight + 24
                                radius:8; color:"#101c2b"
                                RowLayout {
                                    id:deletedRow
                                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:12; spacing:10
                                    ColumnLayout {
                                        Layout.fillWidth:true; spacing:6
                                        Text { text:modelData.name || "PEQ 方案"; color:"#9bb3c5"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:"移除於 " + app.formatTime(modelData.deleted_at || modelData.created_at); color:"#68849c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                    }
                                    RPButton { objectName:"restorePeq_" + modelData.id; text:"復原"; compact:true; enabled:!s.busy; onClicked:bridge.restorePeq(modelData.id) }
                                }
                            }
                        }
                        Rectangle { visible:(s.baselines || []).length > 0; Layout.fillWidth:true; height:1; color:"#2b4052"; Layout.topMargin:7 }
                        SectionTitle { visible:(s.baselines || []).length > 0; title:"Baseline 快照"; subtitle:"查看每版建立當時的量測、Cal 與環境設定。"; Layout.fillWidth:true }
                        Repeater {
                            model:s.baselines || []
                            delegate:Rectangle {
                                required property var modelData
                                Layout.fillWidth:true; height:70; radius:8; color:"#102130"
                                RowLayout {
                                    anchors.fill:parent; anchors.margins:12; spacing:12
                                    ColumnLayout {
                                        Layout.fillWidth:true; spacing:6
                                        Text { text:"Baseline v" + modelData.version + " · " + (modelData.measurements || []).length + " 筆量測"; color:"#bfd6e4"; font.pixelSize:12 }
                                        Text { text:app.formatTime(modelData.created_at); color:"#7895ad"; font.pixelSize:10 }
                                    }
                                    RPButton { objectName:"inspectBaseline_" + modelData.version; text:"查看 →"; compact:true; variant:"ghost"; onClicked:page.inspectBaseline(modelData) }
                                }
                            }
                        }
                    }
                }
                RPCard {
                    Layout.fillWidth:true
                    Layout.preferredWidth:510
                    Layout.alignment:Qt.AlignTop
                    implicitHeight:Math.max(350,timelineColumn.implicitHeight + 40)
                    ColumnLayout {
                        id:timelineColumn
                        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:20
                        spacing:17
                        SectionTitle { title:"操作時間軸"; subtitle:"從建立空間到最後一筆補錄，每個步驟都有紀錄。"; Layout.fillWidth:true }
                        EmptyState { visible:!(s.history || []).length; Layout.fillWidth:true; Layout.preferredHeight:230; symbol:"↺"; title:"旅程尚未開始"; subtitle:"建立專案後，自動保存重要操作與條件變更。" }
                        Repeater {
                            model:s.history || []
                            delegate:RowLayout {
                                required property var modelData
                                Layout.fillWidth:true
                                spacing:13
                                ColumnLayout {
                                    Layout.alignment:Qt.AlignTop
                                    spacing:6
                                    Rectangle { width:8; height:8; radius:4; color:"#75bda9"; Layout.topMargin:4 }
                                    Rectangle { Layout.alignment:Qt.AlignHCenter; width:1; height:55; color:"#2d4658" }
                                }
                                ColumnLayout {
                                    Layout.fillWidth:true; Layout.alignment:Qt.AlignTop; spacing:5
                                    Text { text:modelData.title || "專案更新"; color:"#c6dae6"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                    Text { text:modelData.detail || ""; visible:!!modelData.detail; color:"#819db4"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                                    Text { text:app.formatTime(modelData.time); color:"#57768f"; font.pixelSize:10; Layout.fillWidth:true; elide:Text.ElideRight }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    InspectDialog { id:snapshotDialog; objectName:"baselineSnapshotDialog" }
    PeqDeleteDialog { id:deleteDialog; objectName:"historyPeqDeleteDialog" }
}
