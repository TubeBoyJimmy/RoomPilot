import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: page
    objectName: "peqPage"
    required property var app
    property var s: app.s
    property var peq: (s.candidate_preview || {}).is_draft ? s.candidate_preview : app.selectedPeq
    property bool isDraft: !!peq.is_draft
    property var candidates: ((s.candidate_comparison || {}).candidates || []).length ? s.candidate_comparison : (peq.candidate_selection || {})
    property var saved: (app.project.settings || {}).peq_settings || ({})
    property string settingsProjectId:app.project.id || ""
    onSettingsProjectIdChanged:Qt.callLater(loadLowCutLimit)
    function loadLowCutLimit() {
        var value=(app.project.settings || {}).peq_low_cut_limit_db;
        lowCutLimit.text=String(value !== undefined ? value : 1);
    }
    property bool advanced: false
    property string editChannel: ""
    property int editIndex: 0
    property bool editingAllowed: false
    property var comparison: peq.verification || ({})
    function currentSettings() {
        return {bands:Number(bands.text), independent:channelMode.currentIndex === 1,
            f_min:Number(fMin.text), f_max:Number(fMax.text), max_cut:Number(maxCut.text),
            max_total_cut:Number(totalCut.text), min_q:Number(minQ.text), max_q:Number(maxQ.text),
            gain_step:Number(gainStep.text), freq_step:Number(freqStep.text), q_step:Number(qStep.text),
            target_level:target.text.trim() ? Number(target.text) : null,
            target_ref_min:Number(targetRefMin.text), target_ref_max:Number(targetRefMax.text),
            strategy:["bass_first","extend_existing","joint"][strategyChoice.currentIndex], bass_drift_limit_db:0.5,
            mode:searchMode.currentIndex === 1 ? "deep" : "standard", allow_boost:allowBoost.checked,
            max_boost:Number(maxBoost.text), allow_extended:allowExtended.checked, sample_rate:Number(sampleRate.text)};
    }
    function filterRows() {
        var result = [], filters = peq.filters || {};
        var channels = Object.keys(filters);
        for (var c = 0; c < channels.length; c++) {
            var channel = channels[c];
            for (var i = 0; i < filters[channel].length; i++) result.push({channel:channel, index:i, filter:filters[channel][i]});
        }
        return result;
    }
    function startEdit(row) {
        if(isDraft) return;
        editChannel = row.channel; editIndex = row.index;
        editFrequency.text = String(row.filter.frequency); editGain.text = String(row.filter.gain);
        editQ.text = String(row.filter.q); editEnabled.checked = row.filter.enabled !== false;
        editDialog.open();
    }
    function inspectBand(row) {
        var entries=(peq.explanation || {}).bands || [], band={};
        for(var i=0;i<entries.length;i++) if(entries[i].channel === row.channel && entries[i].index === row.index+1) { band=entries[i];break; }
        var lines=["頻率 " + app.num(row.filter.frequency,1) + " Hz · Gain " + app.num(row.filter.gain,1) + " dB · Q " + app.num(row.filter.q,2), "", "只關閉這一段，其他 Band 保持不變；不會重新最佳化其他參數。"];
        if(!band.channel) lines.push("", "此版本尚未提供逐段評估。可產生新候選以取得相同標準的比較。");
        else {
            var withSummary=(band.with_band || {}).summary || {}, withoutSummary=(band.without_band || {}).summary || {};
            withSummary=withSummary.after || withSummary;withoutSummary=withoutSummary.after || withoutSummary;
            lines.push("", "此段的收益與代價", "殘留波峰，全部已量位置（無此段 → 有此段）：", app.num(withoutSummary.residual_peak_rms_db,3) + " → " + app.num(withSummary.residual_peak_rms_db,3) + " dB RMS", "原低點額外減益，各位置 RMS 最大值：", app.num(withoutSummary.worst_below_target_cut_rms_db,3) + " → " + app.num(withSummary.worst_below_target_cut_rms_db,3) + " dB RMS", "兩項指標的分母不同，不可把收益與代價直接相加。");
            var native=band.native_cost || {}, costsWith=(native.with_band || {}).components || native.with_band || {}, costsWithout=(native.without_band || {}).components || native.without_band || {}, benefit=native.benefit || {};
            if(costsWith.value !== undefined) {
                lines.push("", (peq.explanation || {}).post_hoc ? "本次事後評估的聲道成本（非 dB）" : "此方案的聲道成本（非 dB）", "不含此段：" + app.num(costsWithout.value,4), "包含此段：" + app.num(costsWith.value,4));
                if(benefit.value !== undefined) lines.push("此段使成本" + (benefit.value >= 0 ? "下降 " : "增加 ") + app.num(Math.abs(benefit.value),4));
                var components=[{key:"correction_error",label:"基本修正誤差"},{key:"overshoot",label:"額外削過頭懲罰"},{key:"effort",label:"修正幅度成本"},{key:"complexity",label:"Band 數成本"},{key:"unsupported_boost",label:"未獲支持的增益懲罰"}];
                lines.push("", "成本分項：無此段 → 有此段");
                for(var n=0;n<components.length;n++) {
                    var part=components[n], beforeCost=costsWithout[part.key], afterCost=costsWith[part.key];
                    if(part.key === "unsupported_boost" && !beforeCost && !afterCost) continue;
                    if(beforeCost !== undefined || afterCost !== undefined) lines.push(part.label + "：" + app.num(beforeCost,4) + " → " + app.num(afterCost,4));
                }
                lines.push("此數值包含模型設定的懲罰與 Band 成本；不直接用來排名採用不同取捨設定的候選。");
            }
            var positions=(band.with_band || {}).positions || [], original=(band.without_band || {}).positions || [];
            if(positions.length) lines.push("", "逐位置：無此段 → 有此段");
            for(var p=0;p<positions.length;p++) {
                var afterPosition=positions[p], beforePosition={};
                for(var k=0;k<original.length;k++) if(original[k].id === afterPosition.id) {beforePosition=original[k];break;}
                var afterValues=afterPosition.after || afterPosition, beforeValues=beforePosition.after || beforePosition;
                lines.push(afterPosition.id || afterPosition.channel + " · " + afterPosition.position, "    殘留波峰：" + app.num(beforeValues.residual_peak_rms_db,3) + " → " + app.num(afterValues.residual_peak_rms_db,3) + " dB RMS", "    原低點額外減益：" + app.num(beforeValues.below_target_cut_rms_db,3) + " → " + app.num(afterValues.below_target_cut_rms_db,3) + " dB RMS");
            }
            if(band.frozen) lines.push("", "此段從原方案保留，延伸搜尋未修改它。");
            if((band.labels || []).length) lines=lines.concat(["", "限制與判讀"],band.labels);
            else if((band.boundary_flags || []).length) lines.push("", "此段觸及部分參數限制，請查看方案提醒。");
            if(band.enabled === false) lines.push("", "這段目前停用，對合成曲線沒有貢獻。");
        }
        if((peq.explanation || {}).post_hoc) lines.push("", "此為舊方案的事後計算說明；原始參數未改動。");
        bandDialog.heading=channelLabel(row.channel) + " · Band " + (row.index+1) + " 的收益與代價";
        bandDialog.subheading=peq.name || "PEQ";bandDialog.bodyText=lines.join("\n");bandDialog.open();
    }
    function channelLabel(channel) { return channel === "Shared" ? "共用" : channel === "L" ? "左 L" : channel === "R" ? "右 R" : channel; }
    function savedTargetDescription() {
        var settings=peq.settings || {}, reference=peq.target_reference || {};
        var value="目標 " + app.num(peq.target_level,2) + " dB";
        if((peq.metrics || {}).manual_edit) return value + " · 手動調整後驗算";
        if (reference.mode === "inherited" || settings.strategy === "extend_existing") return value + " · 沿用原方案目標";
        if (settings.target_level !== undefined && settings.target_level !== null) return value + " · 手動固定";
        if (reference.mode === "fixed_reference") return value + " · 參考 " + app.num(reference.actual_min,0) + "–" + app.num(reference.actual_max,0) + " Hz";
        if (settings.target_ref_min !== undefined && settings.target_ref_max !== undefined) return value + " · 參考 " + app.num(settings.target_ref_min,0) + "–" + app.num(settings.target_ref_max,0) + " Hz";
        return value + " · 舊版自動目標";
    }
    function inspectComputation() {
        var settings=peq.settings || {}, metrics=peq.metrics || {}, reference=peq.target_reference || {}, allocation=peq.allocation || {}, explanation=peq.explanation || {};
        var strategyNames={bass_first:"低頻優先，再以剩餘 Band 擴充",extend_existing:"保留原方案與目標，擴充高頻",joint:"整個頻段重新最佳化"};
        var lines=[isDraft ? "目前顯示的未儲存候選" : "目前顯示的已保存方案",peq.name || "PEQ","Baseline v" + (peq.baseline_version || 1),"計算模型：" + (peq.algorithm_version || "舊版未記錄"),"",page.savedTargetDescription(),"分配策略：" + (metrics.manual_edit ? "手動調整後驗算（不保證保留策略）" : strategyNames[settings.strategy] || "舊版搜尋策略"),"校正範圍：" + app.num(settings.f_min,0) + "–" + app.num(settings.f_max,0) + " Hz","搜尋方式：" + (metrics.manual_edit ? "手動指定參數，僅重新驗算" : settings.mode === "deep" ? "深入搜尋" : "快速／標準搜尋")];
        var channels=Object.keys(peq.filters || {});
        if(reference.requested_min !== undefined && reference.requested_max !== undefined) {
            lines.push("目標參考設定：" + app.num(reference.requested_min,1) + "–" + app.num(reference.requested_max,1) + " Hz");
            if(reference.actual_min !== undefined && reference.actual_max !== undefined) lines.push("實際參考資料：" + app.num(reference.actual_min,1) + "–" + app.num(reference.actual_max,1) + " Hz");
            if(reference.coverage_complete === false) lines.push("參考資料未完整覆蓋所設定範圍，請查看方案提醒。");
        }
        if(reference.statistic) lines.push("目標來源：" + reference.statistic);
        if(reference.mode === "fixed_reference") lines.push("第 35 百分位是本程式的起始估計方法，不是聲學標準；可手動指定並鎖定目標。");
        if(reference.mode === "inherited" && reference.original && reference.original.statistic) lines.push("原目標來源：" + reference.original.statistic);
        var evaluationPolicy=explanation.evaluation_policy || {}, scale=evaluationPolicy.overshoot_scale;
        if(scale === undefined) scale=settings.overshoot_scale;
        if(scale !== undefined) {
            lines.push("", explanation.post_hoc ? "本次事後評估的成本設定（非舊版生成政策）" : "此方案的成本設定", "額外削過頭懲罰倍率：" + app.num(scale,2) + "（產品預設 1.00）", "成本為無單位的模型排序數值，並非 dB。", "基本平方誤差 ＋ 額外削過頭懲罰 ＋ 0.0036 × EQ 平方平均 ＋ 0.025 × 啟用 Band 數；進階增益另含未獲支持增益的懲罰。", "基本誤差針對預期修正曲線，不是主圖的平坦目標 RMSE。削過頭懲罰亦依原始曲線低於目標的幅度加權。", "0.25 dB 容差、1/48 octave 平滑、上述權重與 Band 成本均為產品設定，不是聲學標準。");
        }
        lines.push("", "Band 分配");
        for(var i=0;i<channels.length;i++) {
            var channel=channels[i], bands=(peq.filters[channel] || []).filter(function(f){return f.enabled !== false;}).length;
            lines.push(page.channelLabel(channel) + "：" + bands + " 個已啟用 Band / " + (settings.bands || "—") + " 個可用");
            var frozen=(allocation.frozen_counts || {})[channel], remaining=(allocation.remaining_counts || {})[channel], drift=(allocation.bass_drift_db || {})[channel];
            if(frozen !== undefined) lines.push("    保留 " + frozen + " 個，新增 " + Math.max(0,bands-frozen) + " 個" + (remaining !== undefined ? "，剩餘 " + remaining + " 個" : ""));
            else if(remaining !== undefined) lines.push("    剩餘 " + remaining + " 個");
            if(drift !== undefined) lines.push("    擴充造成的低頻曲線變動：" + app.num(drift,3) + " dB");
        }
        if(typeof allocation.bass_drift_db === "number") lines.push("擴充造成的低頻曲線變動：" + app.num(allocation.bass_drift_db,3) + " dB");
        if((allocation.skipped || []).length) { lines.push("", "未採用的擴充"); lines=lines.concat(allocation.skipped); }
        var hasRounds=false;
        for(var c=0;c<channels.length;c++) {
            var stages=((allocation.channels || {})[channels[c]] || {}).stages || [];
            for(var st=0;st<stages.length;st++) {
                var rounds=(stages[st].search || {}).rounds || [];
                if(!rounds.length) continue;
                if(!hasRounds) {lines.push("", "新增 Band 輪次摘要（本方案政策的成本，非 dB）", "新增輪次之後仍有聯合精修、合併與冗餘移除檢查，最終結果可能不同。");hasRounds=true;}
                lines.push("", page.channelLabel(channels[c]) + " · " + (stages[st].name || "搜尋階段"));
                for(var r=0;r<rounds.length;r++) {
                    var round=rounds[r], beforeRound=(round.before_cost || {}).value, nextRound=(round.candidate_cost || {}).value;
                    lines.push("第 " + (round.round || r+1) + " 輪新增搜尋 · " + (round.accepted ? "接受" : "未接受"));
                    if(round.reason) lines.push("    " + round.reason);
                    if(beforeRound !== undefined) lines.push("    原成本 " + app.num(beforeRound,4) + (nextRound !== undefined ? " → 候選 " + app.num(nextRound,4) : "；本輪沒有候選成本"));
                }
                var finalCost=((stages[st].search || {}).final_cost || {}).value;
                if(finalCost !== undefined) lines.push("    此階段經精修及冗餘檢查後的最終成本：" + app.num(finalCost,4));
            }
        }
        lines.push("", "計算工作量");
        if(metrics.objective_evaluations !== undefined) lines.push("目標函數評估：" + metrics.objective_evaluations + " 次");
        var elapsed=metrics.elapsed_seconds !== undefined ? metrics.elapsed_seconds : metrics.elapsed_s;
        if(elapsed !== undefined) lines.push("計算時間：" + app.num(elapsed,2) + " 秒");
        else lines.push("計算時間：此版本未記錄");
        lines.push("", "產生時的限制", "單段減益上限：" + app.num(settings.max_cut,1) + " dB", "總減益上限：" + app.num(settings.max_total_cut,1) + " dB", "Q：" + app.num(settings.min_q,2) + "–" + app.num(settings.max_q,2), "濾波器模擬取樣率：" + app.num(settings.sample_rate,0) + " Hz", "", "搜尋次數有限；停止只表示這次搜尋沒有接受更多濾波器，不能證明不存在其他可改善的組合。", "左側設定用於產生候選；預覽不會新增專案 PEQ，選定後才儲存一個版本。");
        if((explanation.notes || []).length) lines=lines.concat(["", "指標說明"],explanation.notes);
        computationDialog.heading="目前方案的計算依據";
        computationDialog.subheading=isDraft ? "候選預覽 · 選定後才保存" : app.formatTime(peq.created_at) + " · 每個版本分別保存設定與結果";
        computationDialog.bodyText=lines.join("\n");
        computationDialog.open();
    }
    function inspectVerification(record) {
        var value=record.result || record;
        var lines=[value.title || "驗證比較", "", "比較結果"];
        lines=lines.concat(value.details || []);
        var metrics=value.metrics || {};
        if(metrics.improvement_db !== undefined) {
            lines.push("", "形狀偏差（dB）", "修正前：" + app.num(metrics.shape_error_before_db,2), "補錄後：" + app.num(metrics.shape_error_after_db,2), "改善：" + app.num(metrics.improvement_db,2), "實測與預測偏差：" + app.num(metrics.predicted_agreement_db,2));
        }
        var adjustments=value.adjustments || [];
        if(adjustments.length) lines.push("", "後續調整方向");
        for(var i=0;i<adjustments.length;i++) { var a=adjustments[i]; lines.push(channelLabel(a.channel) + " · " + (a.position || "P0") + " · " + app.num(a.frequency,1) + " Hz", a.suggestion || "", a.reason || "", ""); }
        verificationHistoryDialog.heading=(peq.name || "PEQ") + " · 補錄比較紀錄";
        verificationHistoryDialog.subheading=app.formatTime(value.created_at || record.time) + " · 主圖顯示最近一次比較";
        verificationHistoryDialog.bodyText=lines.join("\n");
        verificationHistoryDialog.open();
    }
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: page.availableWidth
        spacing: 18
        ColumnLayout {
            Layout.fillWidth: true; Layout.leftMargin: 30; Layout.rightMargin: 30; Layout.bottomMargin: 25
            spacing: 18
            RPCard {
                visible: !s.baseline_ready
                Layout.fillWidth: true
                implicitHeight: 100
                RowLayout {
                    anchors.fill: parent; anchors.margins: 20; spacing: 20
                    SectionTitle { title: "先建立可信的 Baseline"; subtitle: "確認量測設定與品質後，再讓 PEQ 針對可重現的低頻凸峰提出建議。"; Layout.fillWidth: true }
                    RPButton { text: "前往量測資料  →"; variant: "primary"; onClicked: app.go(1) }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 18
                RPCard {
                    Layout.preferredWidth: 269
                    Layout.minimumWidth: 239
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: controlsColumn.implicitHeight + 38
                    ColumnLayout {
                        id: controlsColumn
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 19
                        spacing: 13
                        SectionTitle { title: "PEQ 比較設定"; subtitle: "先預覽不同取捨，選定後才儲存。"; Layout.fillWidth: true }
                        Text { text: "可分配的 Band 數"; color: "#91a8bd"; font.pixelSize: 11; Layout.topMargin: 4 }
                        RPField { id: bands; objectName: "peqBands"; Layout.fillWidth: true; text: String(saved.bands || 5); validator: IntValidator {bottom:1; top:20} inputMethodHints: Qt.ImhDigitsOnly }
                        Text { text: "左右聲道的設定方式"; color: "#91a8bd"; font.pixelSize: 11 }
                        RPComboBox { id: channelMode; objectName: "peqChannelMode"; Layout.fillWidth: true; model:["左右共用一組", "左右各自設定"]; currentIndex:saved.independent ? 1 : 0 }
                        Text { text: channelMode.currentIndex === 1 ? "Band 數為每聲道可使用數量。" : "同一組濾波器會同時評估 L / R。"; color: "#708ba3"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        RPCheckBox { id: peakSupported; objectName: "confirmPeakSupport"; text: "支援 Peak / Bell（Hz、dB、Q）"; checked: true; Layout.fillWidth: true }
                        Text { text: "固定頻點的圖示 EQ 不能直接套用。使用頻寬 BW 的工具，需先依該工具規則換算 Q。"; color: "#708ba3"; font.pixelSize: 10; Layout.fillWidth: true; wrapMode: Text.WordWrap; lineHeight: 1.4 }
                        Rectangle { Layout.fillWidth: true; height: 1; color: "#2a3c4e"; Layout.topMargin: 4; Layout.bottomMargin: 3 }
                        Text { text: "校正頻段（Hz）"; color: "#91a8bd"; font.pixelSize: 11 }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 9
                            RPField { id:fMin; objectName:"peqFrequencyMin"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.f_min || 30); validator:DoubleValidator {bottom:10; top:20000; locale:"C"} }
                            Text { text:"—"; color:"#708ba3" }
                            RPField { id:fMax; objectName:"peqFrequencyMax"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.f_max || 200); validator:DoubleValidator {bottom:10; top:20000; locale:"C"} }
                        }
                        Text { text:"Band 分配策略"; color:"#91a8bd"; font.pixelSize:11 }
                        RPComboBox { id:strategyChoice; objectName:"peqStrategy"; Layout.fillWidth:true; model:["低頻優先，餘額擴充", "保留原案，擴充高頻", "整段重新最佳化"]; currentIndex:saved.strategy === "extend_existing" ? 1 : saved.strategy === "joint" ? 2 : 0 }
                        Text { text:strategyChoice.currentIndex === 1 ? (isDraft ? "請先儲存這個候選，或選取一個已儲存方案，再進行保留擴充。" : peq.id ? "保留「" + peq.name + "」的目標與全部濾波器，僅以剩餘 Band 處理原範圍以上。Band 不足時保留原方案並提示。" : "請先選取已儲存的 PEQ 作為擴充起點。") : strategyChoice.currentIndex === 2 ? "重新分配整個頻段的 Band，原本的低頻參數也可能改變。目標參考範圍仍獨立設定。" : "先處理 200 Hz 以下，再評估較高頻段。若 Band 不足，會保留低頻並提示尚未處理的區域。"; color:strategyChoice.currentIndex === 2 ? "#dfc28c" : "#708ba3"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        Text { text: "單段最大減益（dB）"; color: "#91a8bd"; font.pixelSize: 11 }
                        RPField { id:maxCut; objectName:"peqMaxCut"; Layout.fillWidth:true; text:String(saved.max_cut || 6); validator:DoubleValidator {bottom:0.1; top:24; locale:"C"} }
                        Text { text:"原低於目標頻點的額外減益容許量（dB RMS）"; color:"#91a8bd"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                        RPField { id:lowCutLimit; objectName:"peqLowCutLimit"; Layout.fillWidth:true; text:"1"; Component.onCompleted:page.loadLowCutLimit(); validator:DoubleValidator {bottom:0; top:6; locale:"C"} }
                        Text { text:"比較各位置 RMS 的最大值，並非每個頻點最多下降此值。這是你選擇候選的容許門檻，不是聲學標準。"; color:"#708ba3"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        Text { text: "搜尋方式"; color: "#91a8bd"; font.pixelSize: 11 }
                        RPComboBox { id:searchMode; objectName:"peqSearchMode"; Layout.fillWidth:true; model:["快速預覽", "深入搜尋（預設）"]; currentIndex:saved.mode === "standard" ? 0 : 1 }
                        Text { text: searchMode.currentIndex === 1 ? "探索更多候選組合，等待時間較長；不保證能改善所有量測。" : "多起點搜尋與局部精修，優先處理最值得修正的凸峰。"; color:"#708ba3"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        RPCheckBox { objectName:"peqShowAdvanced"; text: "進階限制與精校"; checked:page.advanced; onClicked:page.advanced=checked; Layout.fillWidth:true }
                        ColumnLayout {
                            visible:page.advanced
                            Layout.fillWidth:true; spacing:11
                            Text { text:"多段總減益上限（dB）"; color:"#91a8bd"; font.pixelSize:11 }
                            RPField { id:totalCut; objectName:"peqTotalCut"; Layout.fillWidth:true; text:String(saved.max_total_cut || 9); validator:DoubleValidator {bottom:0.1; top:36; locale:"C"} }
                            Text { text:"Q 範圍"; color:"#91a8bd"; font.pixelSize:11 }
                            RowLayout {
                                RPField { id:minQ; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.min_q || 0.4); validator:DoubleValidator {bottom:0.05; top:30; locale:"C"} }
                                Text { text:"—"; color:"#708ba3" }
                                RPField { id:maxQ; objectName:"peqMaxQ"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.max_q || 6); validator:DoubleValidator {bottom:0.05; top:30; locale:"C"} }
                            }
                            Text { text:"可輸入步進：Hz / dB / Q"; color:"#91a8bd"; font.pixelSize:11 }
                            RowLayout {
                                spacing:6
                                RPField { id:freqStep; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.freq_step || 1); validator:DoubleValidator {bottom:0.01; top:1000; locale:"C"} }
                                RPField { id:gainStep; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.gain_step || 0.1); validator:DoubleValidator {bottom:0.01; top:6; locale:"C"} }
                                RPField { id:qStep; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.q_step || 0.01); validator:DoubleValidator {bottom:0.001; top:2; locale:"C"} }
                            }
                            Text { text:"目標水平（dB，留空為自動）"; color:"#91a8bd"; font.pixelSize:11 }
                            RPField { id:target; objectName:"peqTargetLevel"; Layout.fillWidth:true; text:saved.target_level !== undefined && saved.target_level !== null ? String(saved.target_level) : ""; placeholderText:"自動估計"; validator:DoubleValidator {bottom:-150; top:180; locale:"C"} }
                            Text { text:"自動目標參考範圍（Hz）"; color:"#91a8bd"; font.pixelSize:11 }
                            RowLayout {
                                Layout.fillWidth:true; spacing:9
                                RPField { id:targetRefMin; objectName:"peqTargetRefMin"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.target_ref_min || 80); validator:DoubleValidator {bottom:10; top:20000; locale:"C"} }
                                Text { text:"—"; color:"#708ba3" }
                                RPField { id:targetRefMax; objectName:"peqTargetRefMax"; Layout.fillWidth:true; Layout.preferredWidth:1; text:String(saved.target_ref_max || 200); validator:DoubleValidator {bottom:10; top:20000; locale:"C"} }
                            }
                            Text { text:"與校正頻段分開設定，擴大校正範圍不會連帶改變此參考範圍。手填目標或保留目前方案時，優先使用固定目標。新增高頻 Band 對低頻的影響限制為 0.5 dB。"; color:"#708ba3"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                            Text { text:"濾波器模擬取樣率（Hz）"; color:"#91a8bd"; font.pixelSize:11 }
                            RPField { id:sampleRate; objectName:"peqSimulationSampleRate"; Layout.fillWidth:true; text:String(saved.sample_rate || 48000); validator:IntValidator {bottom:8000; top:768000} }
                            Text { text:"填實際 DSP 處理取樣率，勿填設備支援上限。只影響曲線模型，不會重取樣播放音訊；低頻分析可先保留 48000。"; color:"#708ba3"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                            RPCheckBox { id:allowExtended; objectName:"peqAllowExtended"; text:"允許校正 200 Hz 以上"; checked:!!saved.allow_extended; Layout.fillWidth:true }
                            RPCheckBox { id:allowBoost; objectName:"peqAllowBoost"; text:"允許增益（進階精校）"; checked:!!saved.allow_boost; Layout.fillWidth:true }
                            RPField { id:maxBoost; visible:allowBoost.checked; Layout.fillWidth:true; text:String(saved.max_boost || 3); placeholderText:"最大增益 dB"; validator:DoubleValidator {bottom:0; top:6; locale:"C"} }
                            Text { visible:allowBoost.checked || allowExtended.checked; text:"先取得同點補錄與左右偏移量測，再評估寬頻一致性；深窄凹洞通常不適合補償。增益方案需預留前級衰減。"; color:"#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        }
                        RPButton { objectName:"generatePeq"; text:"產生候選並比較  →"; variant:"primary"; Layout.fillWidth:true; enabled:!!s.baseline_ready && !s.busy && peakSupported.checked && lowCutLimit.acceptableInput && (strategyChoice.currentIndex !== 1 || (!!peq.id && !isDraft)); onClicked:bridge.generatePeqCandidates(JSON.stringify(page.currentSettings()),Number(lowCutLimit.text)) }
                        Text { text:"同一目標與限制下，比較三種取捨。不強迫填滿 Band；有限搜尋未找到更多方案，不等於沒有其他改善可能。"; color:"#6f899f"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                    }
                }
                ColumnLayout {
                    Layout.fillWidth:true
                    Layout.preferredWidth:650
                    Layout.alignment:Qt.AlignTop
                    spacing:17
                    PeqCandidateComparison { objectName:"peqCandidateComparison"; app:page.app; comparison:page.candidates; preview:page.peq; isPreview:page.isDraft; Layout.fillWidth:true; visible:(page.candidates.candidates || []).length > 0 }
                    RPCard {
                        Layout.fillWidth:true
                        implicitHeight:curveColumn.implicitHeight + 36
                        ColumnLayout {
                            id:curveColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:14
                            RowLayout {
                                Layout.fillWidth:true; spacing:12
                                RPComboBox { objectName:"peqVersionSelector"; Layout.fillWidth:true; model:s.peqs || []; textRole:"name"; displayText:peq.name || "尚未產生方案"; onActivated:function(index){bridge.selectPeq(model[index].id)} }
                                Rectangle { visible:!!peq.id; implicitWidth:statusText.implicitWidth + 16; height:25; radius:6; color:"#1c373d"; Text { id:statusText; anchors.centerIn:parent; text:isDraft ? "未儲存預覽" : app.statusLabel(peq.status); color:"#83dbc5"; font.pixelSize:10 } }
                                RPCheckBox { text:"低頻"; checked:true; onCheckedChanged:peqChart.lowOnly=checked }
                            }
                            RowLayout {
                                visible:!!peq.id
                                Layout.fillWidth:true; spacing:10
                                Text { text:page.savedTargetDescription(); color:"#8da9bc"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                RPButton { objectName:"inspectPeqComputation"; text:"計算依據"; compact:true; variant:"ghost"; onClicked:page.inspectComputation() }
                            }
                            PeqReadableMetrics { objectName:"peqReadableMetrics"; visible:!!peq.id; Layout.fillWidth:true; app:page.app; result:page.peq }
                            Text { text:"量測與校正後預測"; color:"#b9d0df"; font.pixelSize:12; font.weight:Font.DemiBold }
                            FrequencyChart { id:peqChart; objectName:"peqPredictionChart"; Layout.fillWidth:true; Layout.preferredHeight:320; lowOnly:true; curves:peq.id ? ((s.chart || {}).curves || []) : []; frequencyMin:(s.chart || {}).f_min || 20; frequencyMax:(s.chart || {}).f_max || 20000; emptyText:"設定左側 PEQ 能力，產生第一版建議" }
                            Text { visible:!!peq.id; text:comparison.title ? "補錄實測保留原始音量；如有音量對齊曲線，會另以長虛線呈現。上方主要指標仍是原方案的濾波器預測。" : "虛線：Baseline ／ 實線：PEQ 預測 ／ 金色虛線：共同目標。曲線與指標為模型預測，需補錄驗證。"; color:"#7893aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:gainColumn.implicitHeight + 36
                        ColumnLayout {
                            id:gainColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:12
                            SectionTitle { title:"PEQ 合成校正曲線"; subtitle:"直接查看每個頻率實際削減或增益多少。左右可獨立檢視。"; Layout.fillWidth:true }
                            FrequencyChart { objectName:"peqGainChart"; Layout.fillWidth:true; Layout.preferredHeight:300; responseMode:true; curves:(s.peq_chart || {}).curves || []; frequencyMin:(s.peq_chart || {}).f_min || 20; frequencyMax:(s.peq_chart || {}).f_max || 500; emptyText:"此版本尚無 PEQ 曲線" }
                            Text { text:"粗線：所有已啟用 Band 疊加。0 dB 代表該頻率不變；此圖不含前級衰減。「各 Band」可顯示疊加來源。這是濾波器預測，實際效果需補錄。"; color:"#7893aa"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:filterColumn.implicitHeight + 36
                        ColumnLayout {
                            id:filterColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:12
                            RowLayout {
                                Text { text:"完整 PEQ 參數"; color:"#dce8f2"; font.pixelSize:16; font.weight:Font.DemiBold; Layout.fillWidth:true }
                                RPButton { objectName:"exportPeqText"; text:"匯出文字"; compact:true; enabled:!isDraft && !s.busy; onClicked:bridge.exportPeq(peq.id,"text") }
                                RPButton { objectName:"exportPeqCsv"; text:"CSV"; compact:true; enabled:!isDraft && !s.busy; onClicked:bridge.exportPeq(peq.id,"csv") }
                                RPButton { objectName:"deleteSelectedPeq"; text:"移除"; variant:"danger"; compact:true; enabled:!isDraft && !s.busy; onClicked:{deleteDialog.peqId=peq.id;deleteDialog.peqName=peq.name || "PEQ";deleteDialog.open()} }
                            }
                            Rectangle {
                                Layout.fillWidth:true; height:32; radius:6; color:"#1a2d3f"
                                RowLayout {
                                    anchors.fill:parent; anchors.leftMargin:10; anchors.rightMargin:10; spacing:5
                                    Text { text:"聲道"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:45 }
                                    Text { text:"Band"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:37 }
                                    Text { text:"類型"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:40 }
                                    Text { text:"頻率 Hz"; color:"#809ab1"; font.pixelSize:10; Layout.fillWidth:true; horizontalAlignment:Text.AlignRight }
                                    Text { text:"Gain dB"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:65; horizontalAlignment:Text.AlignRight }
                                    Text { text:"Q"; color:"#809ab1"; font.pixelSize:10; Layout.preferredWidth:49; horizontalAlignment:Text.AlignRight }
                                    Item { Layout.preferredWidth:106 }
                                }
                            }
                            Repeater {
                                model:page.filterRows()
                                delegate:Rectangle {
                                    required property var modelData
                                    Layout.fillWidth:true
                                    height:44; radius:5; color:"#102030"
                                    opacity:modelData.filter.enabled === false ? 0.5 : 1
                                    RowLayout {
                                        anchors.fill:parent; anchors.leftMargin:10; anchors.rightMargin:5; spacing:5
                                        Text { text:page.channelLabel(modelData.channel); color:modelData.channel === "R" ? "#94b2df" : "#83dbc5"; font.pixelSize:11; Layout.preferredWidth:45 }
                                        Text { text:String(modelData.index + 1); color:"#b8cedd"; font.pixelSize:12; font.family:"Segoe UI"; Layout.preferredWidth:37 }
                                        Text { text:"Peak"; color:"#8ba4b9"; font.pixelSize:10; Layout.preferredWidth:40 }
                                        Text { text:app.num(modelData.filter.frequency,1); color:"#d5e5ef"; font.pixelSize:12; font.family:"Segoe UI"; Layout.fillWidth:true; horizontalAlignment:Text.AlignRight }
                                        Text { text:(modelData.filter.gain > 0 ? "+" : "") + app.num(modelData.filter.gain,1); color:modelData.filter.gain > 0 ? "#e4c487" : "#d5e5ef"; font.pixelSize:12; font.family:"Segoe UI"; Layout.preferredWidth:65; horizontalAlignment:Text.AlignRight }
                                        Text { text:app.num(modelData.filter.q,2); color:"#d5e5ef"; font.pixelSize:12; font.family:"Segoe UI"; Layout.preferredWidth:49; horizontalAlignment:Text.AlignRight }
                                        RPButton { text:"依據"; objectName:"explainFilter_" + modelData.channel + "_" + modelData.index; compact:true; variant:"ghost"; implicitWidth:48; leftPadding:7; rightPadding:7; onClicked:page.inspectBand(modelData) }
                                        RPButton { text:"編輯"; objectName:"editFilter_" + modelData.channel + "_" + modelData.index; compact:true; variant:"ghost"; implicitWidth:48; leftPadding:7; rightPadding:7; enabled:!isDraft && !s.busy; onClicked:page.startEdit(modelData) }
                                    }
                                }
                            }
                            Text { visible:page.filterRows().length === 0; text:"本次有限搜尋未接受濾波器，可與不套用 PEQ 的參照比較；不代表不存在其他可行組合。"; color:"#9bb5c5"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                            Text { visible:isDraft; text:"此為未儲存候選。選定並儲存後，才可匯出、編輯或記錄套用。"; color:"#dfc28c"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Rectangle {
                                Layout.fillWidth:true; implicitHeight:preampRow.implicitHeight + 20; radius:7; color:"#1a3139"
                                RowLayout {
                                    id:preampRow
                                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:10; spacing:12
                                    Text { text:"前級衰減"; color:"#88bbaa"; font.pixelSize:11 }
                                    Text { text:app.num(peq.preamp_db || 0,1) + " dB"; color:"#d9f1e9"; font.pixelSize:16; font.weight:Font.DemiBold; font.family:"Segoe UI" }
                                    Text { text:"請取代上一版參數，避免重複疊加。"; color:"#7d9eae"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; horizontalAlignment:Text.AlignRight }
                                }
                            }
                            RPButton { objectName:"markPeqApplied"; text:isDraft ? "先儲存候選，再記錄套用" : peq.status === "applied" || peq.status === "verified" ? "✓  已確認套用此版本" : "我已在 DSP 套用此版本"; variant:"primary"; Layout.fillWidth:true; enabled:!isDraft && !s.busy && peq.status !== "applied" && peq.status !== "verified"; onClicked:appliedDialog.open() }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:rationaleColumn.implicitHeight + 36
                        ColumnLayout {
                            id:rationaleColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:18
                            spacing:12
                            Text { text:"建議依據"; color:"#dce8f2"; font.pixelSize:15; font.weight:Font.DemiBold }
                            Repeater { model:peq.rationale || []; delegate:Text { required property string modelData; text:"•  " + modelData; color:"#a3bacc"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.45 } }
                            Repeater { model:peq.warnings || []; delegate:Text { required property string modelData; text:"!  " + modelData; color:"#dfc28c"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.45 } }
                        }
                    }
                    RPCard {
                        visible:!!peq.id
                        Layout.fillWidth:true
                        implicitHeight:verificationColumn.implicitHeight + 38
                        opacity:isDraft ? 0.55 : 1
                        enabled:!isDraft
                        ColumnLayout {
                            id:verificationColumn
                            anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:19
                            spacing:14
                            SectionTitle { title:"套用後，再量一次"; subtitle:"先以相同音量與播放路徑，在 P0 重錄 L/R；再向左右各偏移 10 cm，檢查聆聽區域的一致性。"; Layout.fillWidth:true }
                            Text { text:"確認麥克風 Cal 已載入，且量測訊號確實經過這版 DSP。偏移後仍各錄獨立 L/R；匯入後在量測資料頁確認位置。"; color:"#89a2b9"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                            RowLayout {
                                Layout.fillWidth:true; spacing:10
                                RPButton { objectName:"importVerification"; text:"+  匯入補錄量測"; variant:"primary"; enabled:!s.busy && (peq.status === "applied" || peq.status === "verified"); onClicked:bridge.importFiles("verification",peq.id) }
                                RPButton { objectName:"compareVerification"; text:"比較並取得下一步"; enabled:!s.busy && (peq.status === "applied" || peq.status === "verified"); onClicked:bridge.compareVerification(peq.id) }
                            }
                            Text { visible:peq.status !== "applied" && peq.status !== "verified"; text:"確認套用上方版本後，即可綁定補錄。"; color:"#dfc28c"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Rectangle { visible:!!comparison.title; Layout.fillWidth:true; height:1; color:"#2a3e50"; Layout.topMargin:3 }
                            Text { visible:!!comparison.title; text:comparison.title || ""; color:"#cce9df"; font.pixelSize:14; font.weight:Font.DemiBold; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Repeater { model:comparison.details || []; delegate:Text { required property string modelData; text:modelData; color:"#98b2c6"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.45 } }
                            RPButton { objectName:"reviewVerificationOverride"; visible:!!comparison.allow_override; text:"檢視差異，仍進行參考比較"; enabled:!s.busy; Layout.fillWidth:true; onClicked:verificationOverrideDialog.open() }
                            Repeater {
                                model:comparison.adjustments || []
                                delegate:Rectangle {
                                    required property var modelData
                                    Layout.fillWidth:true
                                    implicitHeight:adjustmentColumn.implicitHeight + 22
                                    radius:7; color:"#102331"
                                    ColumnLayout {
                                        id:adjustmentColumn
                                        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:11; spacing:6
                                        Text { text:page.channelLabel(modelData.channel) + " · " + (modelData.position || "P0") + " · " + app.num(modelData.frequency,1) + " Hz"; color:"#8cccb8"; font.pixelSize:11 }
                                        Text { text:modelData.suggestion || ""; color:"#c0d9e5"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:modelData.reason || ""; color:"#7e9caf"; font.pixelSize:10; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.4 }
                                    }
                                }
                            }
                            Rectangle { visible:(peq.verification_history || []).length > 0; Layout.fillWidth:true; height:1; color:"#2b4052"; Layout.topMargin:5 }
                            Text { visible:(peq.verification_history || []).length > 0; text:"歷次補錄比較 · 主圖顯示最近一次"; color:"#abc9d9"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                            Repeater {
                                model:peq.verification_history || []
                                delegate:RowLayout {
                                    required property var modelData
                                    required property int index
                                    Layout.fillWidth:true; spacing:10
                                    ColumnLayout {
                                        Layout.fillWidth:true; spacing:4
                                        Text { text:modelData.title || (modelData.result || {}).title || "補錄比較"; color:"#a8c2d2"; font.pixelSize:11; Layout.fillWidth:true; wrapMode:Text.WordWrap }
                                        Text { text:app.formatTime(modelData.created_at || modelData.time); color:"#6e8da5"; font.pixelSize:10 }
                                    }
                                    RPButton { objectName:"inspectVerification_" + index; text:"查看"; compact:true; variant:"ghost"; onClicked:page.inspectVerification(modelData) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    Dialog {
        id:editDialog
        objectName:"editFilterDialog"
        parent:Overlay.overlay
        anchors.centerIn:parent
        width:460; modal:true; padding:25
        background:Rectangle { color:"#142335"; radius:14; border.color:"#3a5063" }
        contentItem:ColumnLayout {
            spacing:16
            SectionTitle { title:"編輯 " + page.channelLabel(page.editChannel) + " · Band " + (page.editIndex + 1); subtitle:"儲存後重新計算預測，並保留原方案供回顧。"; Layout.fillWidth:true }
            RowLayout {
                Layout.fillWidth:true; spacing:12
                ColumnLayout { Layout.fillWidth:true; Layout.preferredWidth:1; Text { text:"頻率 Hz"; color:"#91a8bd"; font.pixelSize:11 } RPField { id:editFrequency; objectName:"editFilterFrequency"; Layout.fillWidth:true; validator:DoubleValidator {bottom:10; top:20000; locale:"C"} } }
                ColumnLayout { Layout.fillWidth:true; Layout.preferredWidth:1; Text { text:"Gain dB"; color:"#91a8bd"; font.pixelSize:11 } RPField { id:editGain; objectName:"editFilterGain"; Layout.fillWidth:true; validator:DoubleValidator {bottom:-24; top:12; locale:"C"} } }
                ColumnLayout { Layout.fillWidth:true; Layout.preferredWidth:1; Text { text:"Q"; color:"#91a8bd"; font.pixelSize:11 } RPField { id:editQ; objectName:"editFilterQ"; Layout.fillWidth:true; validator:DoubleValidator {bottom:0.05; top:30; locale:"C"} } }
            }
            RPCheckBox { id:editEnabled; text:"啟用這個濾波器"; checked:true }
            RowLayout {
                Layout.fillWidth:true; Item { Layout.fillWidth:true }
                RPButton { text:"取消"; onClicked:editDialog.close() }
                RPButton { objectName:"saveFilterEdit"; text:"儲存並重新驗算"; variant:"primary"; enabled:editFrequency.acceptableInput && editGain.acceptableInput && editQ.acceptableInput; onClicked:{bridge.editFilter(page.editChannel,page.editIndex,Number(editFrequency.text),Number(editGain.text),Number(editQ.text),editEnabled.checked);editDialog.close()} }
            }
        }
    }
    Dialog {
        id:appliedDialog
        objectName:"markAppliedDialog"
        parent:Overlay.overlay
        anchors.centerIn:parent
        width:500; modal:true; padding:25
        background:Rectangle { color:"#142335"; radius:14; border.color:"#3a5063" }
        contentItem:ColumnLayout {
            spacing:17
            SectionTitle { title:"確認 PEQ 已套用"; subtitle:peq.name || ""; Layout.fillWidth:true }
            Text { text:"請核對所有聲道的頻率、Gain、Q 與前級衰減，取代舊版濾波器，並確認 DSP 已啟用。這個操作只記錄你的確認，不會直接控制音訊設備。"; color:"#a7bfd0"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.5 }
            RowLayout {
                Layout.fillWidth:true; Item { Layout.fillWidth:true }
                RPButton { text:"返回檢查"; onClicked:appliedDialog.close() }
                RPButton { objectName:"confirmPeqApplied"; text:"已核對並套用"; variant:"primary"; onClicked:{bridge.markApplied(peq.id);appliedDialog.close()} }
            }
        }
    }
    InspectDialog { id:verificationHistoryDialog; objectName:"verificationHistoryDialog" }
    InspectDialog { id:computationDialog; objectName:"peqComputationDialog" }
    InspectDialog { id:bandDialog; objectName:"peqBandExplanationDialog" }
    PeqDeleteDialog { id:deleteDialog; objectName:"peqDeleteDialog" }
    Dialog {
        id:verificationOverrideDialog
        objectName:"verificationOverrideDialog"
        parent:Overlay.overlay
        anchors.centerIn:parent
        width:540; modal:true; padding:25
        background:Rectangle { color:"#142335"; radius:14; border.color:"#3a5063" }
        contentItem:ColumnLayout {
            spacing:15
            SectionTitle { title:"略過設定差異，進行參考比較"; subtitle:"差異仍會保留在紀錄中，不會視為已通過檢查。"; Layout.fillWidth:true }
            ScrollView {
                Layout.fillWidth:true; Layout.preferredHeight:Math.min(220,overrideDetails.implicitHeight)
                contentWidth:availableWidth; clip:true
                Text { id:overrideDetails; width:parent.width; text:(comparison.details || []).join("\n\n"); color:"#dfc28c"; font.pixelSize:12; wrapMode:Text.WordWrap; lineHeight:1.4 }
            }
            Text { text:"若只是更換分析電腦，或已確認記錄差異不影響這次比較，可以繼續。結果會標示為暫定參考，不能據此認定校正已獲驗證。"; color:"#a7bfd0"; font.pixelSize:12; Layout.fillWidth:true; wrapMode:Text.WordWrap; lineHeight:1.5 }
            RowLayout {
                Layout.fillWidth:true; Item { Layout.fillWidth:true }
                RPButton { text:"返回"; onClicked:verificationOverrideDialog.close() }
                RPButton { objectName:"confirmVerificationOverride"; text:"了解差異，仍要比較"; variant:"primary"; onClicked:{bridge.compareVerificationWithOverride(peq.id);verificationOverrideDialog.close()} }
            }
        }
    }
}
