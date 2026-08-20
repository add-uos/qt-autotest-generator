# 各项目 High 方法明细

## dde-calendar — 130 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| upDateInfoShow | CGraphicsView | complexity:20, cognitive:63, lines:110, transitive_loop_depth:3, linear_scan_in_loop:3, loop_count:6, alloc_in_loop:1, in_degree:2 |
| upDateInfoShow | CAllDayEventWeekView | complexity:32, cognitive:72, lines:124, transitive_loop_depth:3, linear_scan_in_loop:8, loop_count:10, alloc_in_loop:4 |
| GetCalendarView | CalendarAdaptor | complexity:13, cognitive:45, lines:91, linear_scan_in_loop:1, loop_count:5, alloc_in_loop:4 |
| QuerySchedules | CalendarAdaptor | complexity:16, cognitive:74, lines:96, linear_scan_in_loop:3, loop_count:6, alloc_in_loop:4 |
| sortAndFilter | CWeekScheduleView | complexity:11, cognitive:41, lines:88, transitive_loop_depth:3, linear_scan_in_loop:2, alloc_in_loop:2 |
| GetReminders | CalendarAdaptor | complexity:8, cognitive:25, lines:58, transitive_loop_depth:3, alloc_in_loop:1 |
| convertSchedules | DSchedule | complexity:14, cognitive:38, lines:98, linear_scan_in_loop:1, alloc_in_loop:3 |
| deleteSchedule | CScheduleOperation | complexity:11, cognitive:37, lines:90, in_degree:2, name_pattern:deleteSchedule |
| fromJsonListString | DScheduleType | complexity:19, cognitive:55, lines:85, linear_scan_in_loop:16, alloc_in_loop:1 |
| getScheduleTimesOn | DAccountModule | complexity:13, cognitive:40, lines:85, linear_scan_in_loop:2, alloc_in_loop:3 |
| paintBackground | CAllDayScheduleItem | complexity:13, cognitive:22, lines:124, linear_scan_in_loop:1, alloc_in_loop:1 |
| paintBackground | CScheduleItem | complexity:21, cognitive:47, lines:205, linear_scan_in_loop:2, alloc_in_loop:1 |
| paintBackground | CMonthScheduleItem | complexity:15, cognitive:24, lines:150, linear_scan_in_loop:1, alloc_in_loop:1 |
| paintEvent | CScheduleSearchItem | complexity:14, cognitive:18, lines:142, linear_scan_in_loop:1, alloc_in_loop:2 |
| queryOldJobData | DDataBaseManagement | complexity:13, cognitive:40, lines:107, linear_scan_in_loop:1, alloc_in_loop:1 |
| repeatJsonResolve | JsonData | complexity:13, cognitive:19, lines:61, linear_scan_in_loop:4, alloc_in_loop:2 |
| scheduleClassificationType | CGraphicsView | complexity:13, cognitive:33, lines:62, linear_scan_in_loop:5, alloc_in_loop:5 |
| setData | CYearScheduleView | complexity:9, cognitive:18, lines:71, linear_scan_in_loop:2, alloc_in_loop:5 |
| splitText | CScheduleItem | complexity:12, cognitive:31, lines:75, linear_scan_in_loop:3, alloc_in_loop:4 |
| updateDateShow | CScheduleSearchView | complexity:17, cognitive:33, lines:118, loop_count:5, alloc_in_loop:1 |
| GetLunarInfo | CalendarAdaptor | complexity:12, cognitive:30, lines:95, alloc_in_loop:2 |
| changeSchedule | CScheduleOperation | complexity:7, cognitive:22, lines:55, in_degree:2 |
| createDB | DAccountDataBase | complexity:10, cognitive:17, lines:60, in_degree:2 |
| event | touchGestureOperation | complexity:10, cognitive:26, lines:54, in_degree:183 |
| event | CWeekHeadView | complexity:9, cognitive:36, recursive, in_degree:3 |
| event | CMonthDayView | complexity:9, cognitive:36, recursive, in_degree:4 |
| fromJsonString | DAccount | complexity:18, cognitive:18, lines:68, in_degree:10 |
| initView | CSettingDialog | complexity:11, cognitive:16, lines:103, linear_scan_in_loop:1 |
| paintCell | CWeekHeadView | complexity:19, cognitive:38, lines:142, alloc_in_loop:1 |
| paintEvent | CScheduleView | complexity:26, cognitive:85, lines:172, linear_scan_in_loop:2 |
| querySchedule | queryScheduleProxy | complexity:21, cognitive:56, lines:115, in_degree:2 |
| resolveTaskJson | semanticAnalysisTask | complexity:14, cognitive:45, lines:71, linear_scan_in_loop:3 |
| slotAutoFeed | CMyScheduleView | complexity:8, lines:81, linear_scan_in_loop:2, alloc_in_loop:1 |
| suggestDatetimeResolve | JsonData | complexity:9, cognitive:21, alloc_in_loop:2, in_degree:2 |
| FilterDayFestival | (free) | complexity:5, linear_scan_in_loop:3, alloc_in_loop:3, in_degree:2 |
| GetDeltaT | (free) | complexity:14, cognitive:105, lines:93, in_degree:4 |
| GetSolarDayFestival | (free) | complexity:7, linear_scan_in_loop:1, alloc_in_loop:3, in_degree:4 |
| getAccessibleName | (free) | complexity:9, cognitive:15, lines:69, linear_scan_in_loop:1 |
| scheduleToJson | (free) | complexity:9, cognitive:15, lines:64, in_degree:2 |
| Calendarmainwindow | Calendarmainwindow | complexity:5, lines:89, in_degree:158 |
| CreateSchedule | CalendarAdaptor | complexity:14, cognitive:28, lines:117 |
| DDataBaseManagement | DDataBaseManagement | complexity:7, cognitive:16, lines:129 |
| DragPressEvent | DragInfoGraphicsView | complexity:9, cognitive:24, lines:67 |
| JosnResolve | JsonData | complexity:9, cognitive:30, linear_scan_in_loop:1 |
| ModifySchedule | CalendarAdaptor | complexity:16, cognitive:53, lines:82 |
| MonthlyScheduleFileter | queryScheduleProxy | complexity:5, linear_scan_in_loop:1, alloc_in_loop:1 |
| SchedulePress | createScheduleTask | complexity:9, cognitive:19, lines:70 |
| UpdateTextList | CenterWidget | complexity:5, linear_scan_in_loop:5, alloc_in_loop:3 |
| WeeklyScheduleFileter | queryScheduleProxy | complexity:5, linear_scan_in_loop:1, alloc_in_loop:1 |
| YearFrame | YearFrame | lines:67, alloc_in_loop:1, in_degree:158 |
| addscheduleitem | scheduleitemwidget | complexity:5, cognitive:15, linear_scan_in_loop:2 |
| changeAllInfo | changeScheduleTask | complexity:9, cognitive:33, lines:58 |
| changeRecurInfo | CScheduleOperation | complexity:13, cognitive:45, lines:111 |
| contextMenuEvent | DragInfoGraphicsView | complexity:8, cognitive:21, lines:71 |
| createDB | DAccountManagerDataBase | complexity:9, cognitive:15, lines:63 |
| createSchedule | CScheduleDlg | complexity:17, cognitive:29, lines:131 |
| createScheduleWithRepeatStatus | createScheduleTask | complexity:10, cognitive:21, lines:57 |
| downloadUidData | SyncStack | complexity:12, cognitive:20, lines:72 |
| drawControl | CalenderStyle | complexity:18, cognitive:76, lines:154 |
| event | CGraphicsScene | complexity:7, recursive, in_degree:7 |
| event | CWeekView | complexity:9, cognitive:36, in_degree:2 |
| event | CScheduleView | complexity:9, cognitive:36, in_degree:21 |
| eventFilter | JobTypeComboBox | complexity:12, cognitive:64, lines:57 |
| eventFilter | CDayMonthWidget | complexity:15, cognitive:68, lines:86 |
| focusItemDeal | CKeyEnableDeal | complexity:8, cognitive:27, lines:50 |
| fromJsonString | DScheduleType | complexity:18, cognitive:21, lines:81 |
| getAllNextYearLunarDayBySolar | LunarDateInfo | complexity:10, cognitive:41, lines:90 |
| getFestivalMonth | DbusHuangLiRequest | complexity:6, linear_scan_in_loop:2, alloc_in_loop:1 |
| getMoveOrientation | AnimationStackedWidget | complexity:8, cognitive:22, in_degree:2 |
| getNewInfo | changeScheduleTask | complexity:13, cognitive:35, lines:85 |
| getTimeLimitByTimeInfo | queryScheduleProxy | complexity:10, cognitive:29, lines:68 |
| initRmindRpeatUI | CScheduleDlg | complexity:8, cognitive:16, lines:51 |
| invoke | ExportedInterface | complexity:11, cognitive:37, lines:64 |
| loadToTmp | SyncStack | complexity:15, cognitive:47, lines:58 |
| mouseDoubleClickEvent | CMonthGraphicsview | complexity:10, cognitive:20, lines:56 |
| mouseMoveEvent | DragInfoGraphicsView | complexity:38, cognitive:144, lines:164 |
| mouseReleaseEvent | DragInfoGraphicsView | complexity:9, cognitive:28, lines:50 |
| notifyMsgHanding | DAccountModule | complexity:11, cognitive:21, lines:58 |
| paint | CMonthDayItem | complexity:23, cognitive:62, lines:210 |
| paintCell | CDayMonthWidget | complexity:10, cognitive:24, lines:79 |
| paintEvent | CMonthDayRectWidget | complexity:10, cognitive:20, lines:101 |
| querOldRemindData | DDataBaseManagement | complexity:5, linear_scan_in_loop:1, alloc_in_loop:1 |
| querySchedulesByKey | DAccountDataBase | complexity:7, lines:51, alloc_in_loop:1 |
| querySchedulesByRRule | DAccountDataBase | complexity:8, cognitive:19, alloc_in_loop:1 |
| resizeEvent | CScheduleSearchView | complexity:5, linear_scan_in_loop:1, in_degree:3 |
| setAccount | ScheduleTypeEditDlg | complexity:7, cognitive:18, lines:51 |
| setData | CWeekScheduleView | complexity:5, transitive_loop_depth:3, alloc_in_loop:1 |
| setDateTime | createScheduleTask | complexity:12, cognitive:33, lines:78 |
| setDateTime | queryScheduleTask | complexity:12, cognitive:36, lines:80 |
| slotCallFinished | DbusAccountManagerRequest | complexity:19, cognitive:65, lines:90 |
| slotCallFinished | DbusAccountManagerRequest | complexity:7, cognitive:18, lines:52 |
| slotCallFinished | DbusAccountRequest | complexity:11, cognitive:44, lines:81 |
| slotCallFinished | DbusAccountRequest | complexity:13, cognitive:64, lines:77 |
| slotGetAccountListFinish | AccountManager | complexity:9, cognitive:23, lines:51 |
| slotMousePress | CYearWindow | complexity:8, cognitive:15, lines:61 |
| slotUidLoginStatueChange | DAccountManageModule | complexity:11, cognitive:27, lines:83 |
| updateHeight | Calendarmainwindow | complexity:8, cognitive:16, in_degree:3 |
| updateSchedule | DAccountModule | complexity:11, cognitive:31, lines:75 |
| updateSchedule | CScheduleView | complexity:5, linear_scan_in_loop:2, alloc_in_loop:2 |
| updateScheduleType | DAccountModule | complexity:9, cognitive:22, lines:67 |
| updateShowSchedule | CDayWindow | linear_scan_in_loop:1, alloc_in_loop:1, in_degree:2 |
| viewportEvent | JobTypeListView | complexity:17, cognitive:65, lines:102 |
| JobToObject | (free) | linear_scan_in_loop:1, alloc_in_loop:1, in_degree:2 |
| applyScheduleUpdates | (free) | complexity:12, cognitive:17, in_degree:2 |
| createViewByIndex | Calendarmainwindow | complexity:11, cognitive:16 |
| downloadTaskhanding | DAccountModule | complexity:10, cognitive:30 |
| eventFilter | CYearWindow | complexity:8, cognitive:22 |
| fromJsonString | DScheduleQueryPar | complexity:11, cognitive:18 |
| getDrawRegion | CScheduleCoorManage | complexity:8, lines:72 |
| getGeneralSettings | DAccountManageModule | complexity:8, cognitive:19 |
| getOldRemindByAlarm | DDE_Calendar | complexity:13, cognitive:25 |
| getRRuleType | DSchedule | complexity:8, cognitive:22 |
| getScheduleByExported | DDE_Calendar | complexity:10, lines:55 |
| insertButton | buttonwidget | complexity:9, cognitive:27 |
| jsonObjectToInfo | CaHuangLiDayInfo | complexity:13, lines:57 |
| mouseReleaseScheduleUpdate | DragInfoGraphicsView | complexity:9, cognitive:22 |
| notifyMsgHanding | DAlarmManager | complexity:9, cognitive:17 |
| queryNonRepeatingSchedule | queryScheduleProxy | complexity:8, cognitive:16 |
| queryWeeklySchedule | queryScheduleProxy | complexity:9, cognitive:25 |
| resizeView | Calendarmainwindow | complexity:8, lines:64 |
| setDateFormat | DCalendarDDialog | complexity:11, cognitive:21 |
| setDateFormatChanged | CalendarManager | complexity:11, cognitive:21 |
| setRRuleType | DSchedule | complexity:10, cognitive:17 |
| slideEvent | DragInfoGraphicsView | complexity:9, cognitive:18 |
| slotImportScheduleType | JobTypeListView | complexity:9, lines:94 |
| slotShowSyncToast | Calendarmainwindow | complexity:8, cognitive:19 |
| slotStextChanged | Calendarmainwindow | complexity:10, cognitive:19 |
| slotTheme | Calendarmainwindow | complexity:8, in_degree:2 |
| startDeferredViewDataInit | Calendarmainwindow | complexity:11, cognitive:24 |
| unionIDDataMerging | DAccountManageModule | complexity:8, lines:64 |

## deepin-calculator — 113 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| reformatSeparatorsPro | Utils | complexity:11, cognitive:24, lines:55, linear_scan_in_loop:2, alloc_in_loop:3, in_degree:4 |
| SetAttrRecur | IconButton | complexity:21, cognitive:55, lines:58, linear_scan_in_loop:1, recursive |
| copyClipboard2Result | ProExpressionBar | complexity:16, cognitive:25, lines:80, linear_scan_in_loop:5, in_degree:2 |
| getFocus | ProgrammerKeypad | complexity:20, cognitive:62, lines:55, loop_count:5, in_degree:8 |
| handleTextChanged | InputEdit | complexity:13, cognitive:15, lines:118, linear_scan_in_loop:3, alloc_in_loop:1 |
| isNumberOutOfRange | ProExpressionBar | complexity:27, cognitive:100, lines:107, alloc_in_loop:5, in_degree:2 |
| paint | SimpleListDelegate | complexity:20, cognitive:58, lines:218, linear_scan_in_loop:2, in_degree:2 |
| pointFaultTolerance | SciExpressionBar | complexity:19, cognitive:32, lines:102, linear_scan_in_loop:4, in_degree:4 |
| pointFaultTolerance | InputEdit | complexity:13, cognitive:22, lines:57, linear_scan_in_loop:2, alloc_in_loop:3 |
| radixChanged | SimpleListModel | complexity:28, cognitive:82, lines:132, loop_count:5, alloc_in_loop:11 |
| scanAndExec | InputEdit | complexity:21, cognitive:70, lines:99, loop_count:5, alloc_in_loop:11 |
| selectedPartDelete | ProExpressionBar | complexity:9, cognitive:21, lines:67, in_degree:2, name_pattern:selectedPartDelete |
| byteArrowListWidgetItemClicked | ProgramModule | complexity:13, cognitive:34, lines:58, in_degree:2 |
| copyClipboard2Result | SciExpressionBar | complexity:10, cognitive:15, lines:65, linear_scan_in_loop:5 |
| deleteText | SciExpressionBar | complexity:12, cognitive:25, lines:93, name_pattern:deleteText |
| enterBackspaceEvent | ProExpressionBar | complexity:11, cognitive:41, lines:80, in_degree:3 |
| enterEqualEvent | ProExpressionBar | complexity:13, cognitive:27, lines:92, in_degree:3 |
| enterNotEvent | ProExpressionBar | complexity:17, cognitive:40, lines:114, linear_scan_in_loop:2 |
| enterNumberEvent | ProExpressionBar | complexity:11, cognitive:22, lines:68, in_degree:3 |
| enterOppositeEvent | ProExpressionBar | complexity:26, cognitive:115, lines:144, linear_scan_in_loop:2 |
| enterSymbolEvent | ProExpressionBar | complexity:12, cognitive:34, lines:69, in_degree:3 |
| eventFilter | MemHisWidget | complexity:18, cognitive:63, lines:61, in_degree:2 |
| expressionInFunc | SciExpressionBar | complexity:15, cognitive:38, lines:93, linear_scan_in_loop:2 |
| formatExpression | SimpleListModel | complexity:12, cognitive:22, lines:54, in_degree:4 |
| handleEditKeyPress | ProgramModule | complexity:88, cognitive:216, lines:287, in_degree:4 |
| handleKeypadButtonPress | ProgramModule | complexity:63, cognitive:149, lines:238, in_degree:3 |
| handleKeypadButtonPressByspace | ProgramModule | complexity:64, cognitive:151, lines:234, in_degree:3 |
| initButtons | ScientificKeyPad | complexity:19, cognitive:90, lines:110, in_degree:2 |
| initButtons | ProgrammerKeypad | complexity:10, cognitive:21, lines:69, in_degree:5 |
| paintspecialbtn | TextButton | complexity:19, cognitive:100, lines:177, in_degree:2 |
| pointFaultTolerance | ExpressionBar | complexity:24, cognitive:44, lines:109, linear_scan_in_loop:4 |
| radixChanged | ProgrammerKeypad | complexity:15, cognitive:38, lines:68, in_degree:5 |
| reformatSeparators | Utils | complexity:5, linear_scan_in_loop:2, alloc_in_loop:3, in_degree:2 |
| setSystem | ProSystemKeypad | complexity:25, cognitive:93, lines:84, loop_count:10 |
| settingLinkage | ExpressionBar | complexity:8, cognitive:20, lines:55, in_degree:2 |
| symbolFaultTolerance | ProExpressionBar | complexity:9, cognitive:21, alloc_in_loop:4, in_degree:3 |
| symbolFaultTolerance | InputEdit | complexity:16, cognitive:39, lines:70, alloc_in_loop:4 |
| BasicModule | BasicModule | complexity:5, lines:131, in_degree:3 |
| CurrentCursorPositionNumber | InputEdit | complexity:10, cognitive:16, in_degree:2 |
| MemHisWidget | MemHisWidget | complexity:8, lines:149, in_degree:3 |
| MemoryWidget | MemoryWidget | complexity:6, lines:74, in_degree:80 |
| ProgramModule | ProgramModule | complexity:6, lines:141, in_degree:79 |
| cutApart | SimpleListDelegate | complexity:9, linear_scan_in_loop:2, alloc_in_loop:2 |
| data | SimpleListModel | complexity:8, cognitive:16, in_degree:2 |
| enterBackspaceEvent | ExpressionBar | complexity:12, cognitive:37, lines:91 |
| enterBackspaceEvent | SciExpressionBar | complexity:26, cognitive:90, lines:155 |
| enterEqualEvent | ExpressionBar | complexity:20, cognitive:44, lines:138 |
| enterOperatorEvent | ProExpressionBar | complexity:11, cognitive:25, lines:79 |
| enterPointEvent | SciExpressionBar | complexity:8, lines:56, in_degree:2 |
| enterSymbolEvent | ExpressionBar | complexity:16, cognitive:42, lines:96 |
| enterSymbolEvent | SciExpressionBar | complexity:13, cognitive:39, lines:75 |
| eventFilter | MemoryWidget | complexity:11, cognitive:33, in_degree:4 |
| expressionCheck | ExpressionBar | complexity:16, cognitive:30, lines:73 |
| expressionCheck | ProExpressionBar | complexity:7, cognitive:16, in_degree:2 |
| expressionCheck | SciExpressionBar | complexity:16, cognitive:28, lines:73 |
| formatExpression | InputEdit | complexity:12, cognitive:22, lines:54 |
| formatThousandsSeparators | Utils | complexity:9, lines:73, in_degree:5 |
| formatThousandsSeparatorsPro | Utils | complexity:10, cognitive:25, in_degree:3 |
| getFocus | ScientificKeyPad | complexity:43, cognitive:181, lines:102 |
| handleEditKeyPress | scientificModule | complexity:85, cognitive:210, lines:314 |
| handleEditKeyPress | BasicModule | complexity:56, cognitive:126, lines:188 |
| handleKeypadButtonPress | scientificModule | complexity:70, cognitive:144, lines:226 |
| handleKeypadButtonPress | BasicModule | complexity:34, cognitive:71, lines:118 |
| handleKeypadButtonPressByspace | scientificModule | complexity:71, cognitive:146, lines:224 |
| handleKeypadButtonPressByspace | BasicModule | complexity:33, cognitive:70, lines:139 |
| init | TextButton | complexity:22, cognitive:136, lines:56 |
| initArrowRectangle | ProgramModule | complexity:15, cognitive:29, lines:195 |
| initButtons | BasicKeypad | complexity:6, cognitive:17, in_degree:2 |
| initConnect | MemoryWidget | complexity:10, cognitive:19, lines:69 |
| judgeinput | ProExpressionBar | complexity:9, cognitive:26, in_degree:3 |
| judgeinput | SciExpressionBar | complexity:10, cognitive:31, lines:60 |
| moveRight | ProExpressionBar | complexity:5, linear_scan_in_loop:1, in_degree:3 |
| paint | ProListDelegate | complexity:11, cognitive:16, lines:85 |
| paintEvent | TextButton | complexity:12, cognitive:21, lines:149 |
| paintEvent | EqualButton | complexity:5, lines:136, in_degree:7 |
| paintEvent | IconButton | complexity:9, cognitive:23, lines:124 |
| paintEvent | MemoryButton | complexity:21, cognitive:56, lines:330 |
| scientificModule | scientificModule | complexity:5, lines:147, in_degree:3 |
| setitemwordwrap | MemoryWidget | complexity:10, cognitive:25, in_degree:2 |
| shear | SciExpressionBar | complexity:12, cognitive:25, lines:95 |
| shiftArrowListWidgetItemClicked | ProgramModule | complexity:8, cognitive:18, in_degree:2 |
| showTextEditMenu | InputEdit | complexity:10, cognitive:16, lines:60 |
| showTextEditMenuByAltM | InputEdit | complexity:10, cognitive:16, lines:60 |
| showtips | MemoryButton | complexity:8, cognitive:26, in_degree:2 |
| switchToSpecialMode | CalculatorInterface | dbus_slot, complexity:6, cognitive:15 |
| symbolComplement | ProExpressionBar | complexity:5, linear_scan_in_loop:13, in_degree:3 |
| symbolComplement | SciExpressionBar | complexity:7, linear_scan_in_loop:21, loop_count:5 |
| themetypechanged | ProgrammerItemWidget | complexity:10, cognitive:30, lines:57 |
| copyClipboard2Result | ExpressionBar | complexity:8, lines:68 |
| enterEvent | TextButton | complexity:14, cognitive:105 |
| enterLeftBracketsEvent | ExpressionBar | complexity:8, lines:54 |
| enterOperatorEvent | SciExpressionBar | complexity:9, lines:68 |
| enterRightBracketsEvent | ExpressionBar | complexity:8, lines:54 |
| getFocus | ProSystemKeypad | complexity:10, cognitive:23 |
| getFocus | MemHisKeypad | complexity:10, cognitive:24 |
| getFocus | ProCheckBtnKeypad | complexity:8, cognitive:16 |
| getFocus | MemoryKeypad | complexity:8, cognitive:16 |
| getFocus | BasicKeypad | complexity:12, cognitive:26 |
| keyPressEvent | MemoryListWidget | complexity:11, cognitive:22 |
| keyPressEvent | SimpleListView | complexity:11, cognitive:24 |
| keyPressEvent | IconButton | complexity:10, cognitive:20 |
| keyPressEvent | ProListView | complexity:11, cognitive:24 |
| leaveEvent | TextButton | complexity:14, cognitive:105 |
| mouseMoveEvent | SimpleListView | complexity:8, cognitive:22 |
| stringIsDigitPro | Utils | complexity:14, cognitive:39 |
| getCurrentMode | CalculatorInterface | dbus_slot |
| hideWindow | CalculatorInterface | dbus_slot |
| quitWindow | CalculatorInterface | dbus_slot |
| raiseWindow | CalculatorInterface | dbus_slot |
| showWindow | CalculatorInterface | dbus_slot |
| switchToProgrammerMode | CalculatorInterface | dbus_slot |
| switchToScientificMode | CalculatorInterface | dbus_slot |
| switchToStandardMode | CalculatorInterface | dbus_slot |

## deepin-compressor — 77 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| getRecommendedAppsByQio | MimesAppsManager | complexity:10, cognitive:25, lines:60, transitive_loop_depth:3, loop_count:5, alloc_in_loop:4 |
| reset | FileTask | complexity:13, cognitive:21, lines:96, recursive, in_degree:2, name_pattern:reset |
| ConstructAddOptionsByThread | CalculateSizeThread | concurrent_class, complexity:7, lines:74, linear_scan_in_loop:1, recursive |
| deleteWhenJobFinish | MainWindow | complexity:11, cognitive:33, lines:61, linear_scan_in_loop:2, name_pattern:deleteWhenJobFinish |
| encode | FastEncL1 | complexity:19, cognitive:45, lines:136, transitive_loop_depth:3, loop_count:7 |
| event | DataTreeView | complexity:22, cognitive:48, lines:152, recursive, in_degree:24 |
| initMimeTypeApps | MimesAppsManager | complexity:20, cognitive:49, lines:162, linear_scan_in_loop:7, alloc_in_loop:2 |
| location | DFMStandardPaths | complexity:34, cognitive:71, lines:98, recursive, in_degree:167 |
| writeCentralDirectory | ZipWriter | complexity:15, cognitive:31, lines:178, alloc_in_loop:7, name_pattern:writeCentralDirectory |
| main | (free) | complexity:48, cognitive:126, lines:283, loop_count:6, alloc_in_loop:1 |
| addNewFiles | UnCompressView | complexity:13, cognitive:36, lines:100, linear_scan_in_loop:2 |
| archive | Archiver | complexity:10, cognitive:19, lines:53, in_degree:40 |
| checkCompressOptionValid | CompressSettingPage | complexity:10, cognitive:15, lines:63, linear_scan_in_loop:1 |
| compress | Archiver | complexity:12, cognitive:19, lines:86, in_degree:7 |
| createDeleteBox | SettingDialog | complexity:7, cognitive:17, lines:65, name_pattern:createDeleteBox |
| data | DataModel | complexity:16, cognitive:45, lines:89, in_degree:39 |
| encode | FastEncL4 | complexity:22, cognitive:52, lines:149, loop_count:8 |
| handleJobNormalFinished | MainWindow | complexity:38, cognitive:132, lines:268, linear_scan_in_loop:2 |
| load | Properties | complexity:5, lines:62, linear_scan_in_loop:1, in_degree:33 |
| paint | StyleTreeViewDelegate | complexity:13, cognitive:20, lines:95, recursive |
| rightExtract2Path | MainWindow | complexity:11, cognitive:23, lines:121, linear_scan_in_loop:1 |
| slotRenameFile | CompressView | complexity:17, cognitive:51, lines:83, recursive |
| sort | DataModel | complexity:16, cognitive:40, lines:77, in_degree:4 |
| supportedWriteMimeTypes | PluginManager | complexity:7, lines:50, in_degree:2, name_pattern:supportedWriteMimeTypes |
| timerEvent | MainWindow | complexity:7, cognitive:22, lines:59, linear_scan_in_loop:2 |
| writeBlockHuff | HuffmanBitWriter | complexity:13, cognitive:19, lines:99, name_pattern:writeBlockHuff |
| writeLocalFileHeader | ZipWriter | complexity:13, cognitive:20, lines:119, name_pattern:writeLocalFileHeader |
| writeTokens | HuffmanBitWriter | complexity:17, cognitive:41, lines:112, name_pattern:writeTokens |
| detectUTF8 | (free) | complexity:9, cognitive:39, lines:52, in_degree:2 |
| determineMimeType | (free) | complexity:17, cognitive:35, lines:163, in_degree:9 |
| main | (free) | complexity:25, cognitive:68, lines:254, linear_scan_in_loop:2 |
| DesktopFile | DesktopFile | complexity:10, lines:93, in_degree:5 |
| checkSettings | MainWindow | complexity:9, cognitive:27, lines:82 |
| createPathBox | SettingDialog | complexity:10, cognitive:38, lines:112 |
| displaySpeedAndTime | ProgressPage | complexity:14, cognitive:57, lines:67 |
| drawRow | DataTreeView | complexity:10, cognitive:15, lines:102 |
| extract | Extractor | complexity:5, alloc_in_loop:1, in_degree:58 |
| filterBy | PluginManager | complexity:6, cognitive:17, linear_scan_in_loop:1 |
| getCurPathFiles | UnCompressView | complexity:5, cognitive:15, linear_scan_in_loop:2 |
| handleApplicationTabEventNotify | MainWindow | complexity:47, cognitive:269, lines:151 |
| handleArguments_Append | MainWindow | complexity:6, lines:84, alloc_in_loop:1 |
| handleArguments_RightMenu | MainWindow | complexity:21, cognitive:79, lines:198 |
| handleFileName | UiTools | complexity:5, cognitive:15, in_degree:2 |
| handleJobCancelFinished | MainWindow | complexity:16, cognitive:38, lines:78 |
| handleJobErrorFinished | MainWindow | complexity:68, cognitive:261, lines:275 |
| initData | OpenWithDialog | complexity:9, lines:79, linear_scan_in_loop:1 |
| mouseMoveEvent | UnCompressView | complexity:13, cognitive:20, lines:103 |
| prepareCompressAliasEntries | MainWindow | complexity:12, cognitive:30, lines:70 |
| readCentralDirectory | ZipReader | complexity:8, lines:70, alloc_in_loop:1 |
| refreshCompressLevel | CompressSettingPage | complexity:29, cognitive:216, lines:113 |
| refreshDataByCurrentPathChanged | UnCompressView | complexity:6, lines:62, linear_scan_in_loop:1 |
| refreshPage | MainWindow | complexity:21, cognitive:48, lines:142 |
| run | CalculateSizeThread | concurrent_class, complexity:6, lines:77 |
| showDialog | RenameDialog | complexity:10, cognitive:18, lines:96 |
| showErrorMessage | MainWindow | complexity:35, cognitive:123, lines:136 |
| slotChoosefiles | MainWindow | complexity:14, cognitive:28, lines:83 |
| slotCompress | MainWindow | complexity:12, cognitive:25, lines:118 |
| slotCompressClicked | CompressSettingPage | complexity:14, cognitive:22, lines:100 |
| slotFinishCalculateSize | MainWindow | complexity:9, cognitive:24, lines:58 |
| slotHandleExtractFinished | ConvertJob | complexity:6, cognitive:16, lines:70 |
| slotHandleExtractFinished | StepExtractJob | complexity:9, cognitive:30, lines:110 |
| slotOpenFileChanged | MainWindow | complexity:9, cognitive:23, lines:50 |
| slotShowRightMenu | CompressView | complexity:5, lines:61, linear_scan_in_loop:1 |
| slotShowRightMenu | UnCompressView | complexity:7, lines:56, linear_scan_in_loop:1 |
| slotTitleCommentButtonPressed | MainWindow | complexity:11, cognitive:28, lines:214 |
| slotUseOtherApplication | OpenWithDialog | complexity:12, cognitive:24, lines:87 |
| updateValue | org_deepin_compressor_method | complexity:28, cognitive:42, lines:144 |
| copyDirectoryRecursively | (free) | complexity:6, recursive, in_degree:2 |
| main | (free) | complexity:20, cognitive:41, lines:118 |
| calSpeedAndRemainingTime | ProgressPage | complexity:8, lines:58 |
| displayNameToEnum | MimeTypeDisplayManager | complexity:9, cognitive:45 |
| initialize | org_deepin_compressor_method | complexity:14, lines:78 |
| readCompressedData | FileTask | complexity:8, cognitive:15 |
| refreshMenu | CompressSettingPage | complexity:9, lines:52 |
| slotCancelClicked | ProgressPage | complexity:8, cognitive:19 |
| slotFailureRetry | MainWindow | complexity:8, cognitive:17 |
| slotHandleArguments | MainWindow | complexity:9, cognitive:18 |

## deepin-draw — 92 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| sortZBaseOneBzItem | PageScene | complexity:13, cognitive:30, lines:90, transitive_loop_depth:3, linear_scan_in_loop:2, loop_count:5, alloc_in_loop:3 |
| firstItem | PageScene | complexity:29, cognitive:86, lines:138, linear_scan_in_loop:3, loop_count:5, alloc_in_loop:1 |
| deserializationToTree_helper | (free) | complexity:13, cognitive:37, lines:79, alloc_in_loop:2, recursive, in_degree:2 |
| doRun | CFileWatcher | concurrent_class, complexity:10, cognitive:27, lines:57, linear_scan_in_loop:1 |
| event | PageScene | complexity:15, cognitive:36, lines:66, recursive, in_degree:58 |
| getGroupTreeInfo | PageScene | complexity:11, cognitive:25, lines:66, alloc_in_loop:2, recursive |
| moveBzItemsLayer | PageScene | complexity:11, cognitive:20, lines:51, linear_scan_in_loop:4, alloc_in_loop:2 |
| noticeUser | CUndoRedoCommandGroup | complexity:18, cognitive:78, lines:65, linear_scan_in_loop:1, alloc_in_loop:3 |
| save | Page | complexity:10, cognitive:23, recursive, in_degree:82, name_pattern:save |
| Create | CDrawToolFactory | complexity:16, cognitive:30, lines:58, in_degree:2 |
| calcPolygon_helper | CGraphicsPolygonalStarItem | complexity:9, cognitive:17, lines:101, alloc_in_loop:5 |
| clear | CFileWatcher | concurrent_class, recursive, in_degree:2, name_pattern:clear |
| deepCopy | CGraphicsUnit | complexity:35, cognitive:93, lines:134, in_degree:2 |
| eventFilter | DrawBoard | complexity:21, cognitive:90, lines:78, in_degree:2 |
| exec | FileSelectDialog | complexity:6, cognitive:17, lines:58, in_degree:8 |
| fromQEvent | CDrawToolEvent | complexity:13, cognitive:25, lines:57, in_degree:3 |
| moveItemsZDown | PageScene | complexity:8, cognitive:16, lines:53, linear_scan_in_loop:2 |
| moveItemsZUp | PageScene | complexity:8, cognitive:16, lines:53, linear_scan_in_loop:2 |
| release | CGraphicsUnit | complexity:26, cognitive:63, lines:81, in_degree:7 |
| slotOnSavePathChange | CExportImageDialog | complexity:12, cognitive:26, in_degree:3, name_pattern:slotOnSavePathChange |
| toolDoUpdate | IDrawTool | complexity:17, cognitive:85, lines:92, in_degree:4 |
| updateHandlesGeometry | CGraphicsItemGroup | complexity:17, cognitive:44, lines:80, linear_scan_in_loop:1 |
| wheelEvent | PageView | complexity:118, cognitive:255, lines:1478, loop_count:8 |
| ~CCmdBlock | CCmdBlock | complexity:11, cognitive:15, lines:69, destructor |
| adaptImgPosAndRect | (free) | complexity:8, cognitive:19, lines:52, in_degree:3 |
| convertToSRgbColorSpace | (free) | complexity:14, cognitive:21, lines:77, in_degree:2 |
| doFocusChanged | (free) | complexity:13, cognitive:40, lines:55, in_degree:2 |
| loadDdfWithNoCombinGroup | (free) | complexity:13, cognitive:51, lines:105, in_degree:2 |
| Shortcut | Shortcut | lines:87, alloc_in_loop:2, in_degree:78 |
| TabBarWgt | TabBarWgt | complexity:5, lines:61, linear_scan_in_loop:1 |
| autoResizeUpdate | CAttriBaseOverallWgt | complexity:6, linear_scan_in_loop:4, in_degree:2 |
| calcPoints_helper | CGraphicsPolygonItem | complexity:6, lines:51, alloc_in_loop:2 |
| cancelGroup | PageScene | complexity:7, cognitive:15, alloc_in_loop:2 |
| decideUpdate | CSelectTool | complexity:12, cognitive:50, lines:96 |
| dueTouchDoubleClickedStart | IDrawTool | complexity:7, cognitive:19, lines:66 |
| eventFilter | CTextTool | complexity:15, cognitive:54, lines:76 |
| getCenter | CGraphicsItemGroup | complexity:11, cognitive:21, lines:70 |
| getColor | ColorLabel | complexity:5, cognitive:15, in_degree:2 |
| getCursor | IDrawTool | complexity:24, cognitive:57, lines:138 |
| getCursor | CSizeHandleRect | complexity:15, cognitive:29, lines:102 |
| getGroupTree | PageScene | complexity:5, alloc_in_loop:3, recursive |
| getStyleOption | QComboxMenuDelegate | complexity:13, cognitive:17, lines:81 |
| initComboBox | TopTilte | complexity:8, cognitive:20, lines:71 |
| initConnection | MainWindow | complexity:5, lines:72, in_degree:2 |
| initConnection | CCutWidget | complexity:17, cognitive:27, lines:135 |
| initUI | DrawToolManager | complexity:8, cognitive:16, lines:63 |
| initUI | CCutWidget | complexity:5, lines:275, linear_scan_in_loop:1 |
| isCurrentZMovable | PageScene | complexity:11, cognitive:28, lines:56 |
| isFileNameLegal | Application | complexity:8, cognitive:16, linear_scan_in_loop:1 |
| loadFiles | DrawBoard | complexity:13, cognitive:27, lines:132 |
| mouseEvent | PageScene | complexity:6, recursive, in_degree:2 |
| paintEvent | ToolButton | complexity:10, cognitive:23, lines:101 |
| releaseBzItemsTreeInfo | PageScene | complexity:5, recursive, in_degree:3 |
| resizeCutSize | CGraphicsCutItem | complexity:19, cognitive:34, lines:192 |
| saveAs | Page | complexity:8, cognitive:21, name_pattern:saveAs |
| setCurrentTool | DrawToolManager | complexity:9, cognitive:17, lines:57 |
| setRatioType | CGraphicsCutItem | complexity:9, cognitive:23, lines:55 |
| sizeHint | ToolButton | complexity:11, cognitive:30, lines:64 |
| toStyle | CTextEdit | complexity:11, cognitive:21, in_degree:2 |
| toWeight | CTextEdit | complexity:9, cognitive:45, in_degree:2 |
| toolCreatItemUpdate | CEllipseTool | complexity:11, cognitive:49, lines:86 |
| toolCreatItemUpdate | CPolygonTool | complexity:11, cognitive:49, lines:83 |
| toolCreatItemUpdate | CRectTool | complexity:11, cognitive:49, lines:81 |
| toolCreatItemUpdate | CPolygonalStarTool | complexity:11, cognitive:49, lines:83 |
| toolCreatItemUpdate | CTriangleTool | complexity:11, cognitive:49, lines:83 |
| toolDoFinish | IDrawTool | complexity:14, cognitive:68, lines:81 |
| toolDoStart | IDrawTool | complexity:7, cognitive:20, lines:70 |
| toolFinish | CSelectTool | complexity:11, cognitive:20, lines:66 |
| toolStart | CSelectTool | complexity:12, cognitive:30, lines:61 |
| toolUpdate | CSelectTool | complexity:9, cognitive:18, lines:55 |
| updateBoundingRect | CGraphicsItemGroup | complexity:7, cognitive:15, lines:59 |
| updateEndPathStyle | CGraphicsPenItem | complexity:12, cognitive:19, lines:108 |
| updateHandlesGeometry | CGraphicsLineItem | complexity:12, cognitive:47, lines:57 |
| updateStartPathStyle | CGraphicsPenItem | complexity:12, cognitive:20, lines:103 |
| zItem | CGraphicsItem | complexity:6, alloc_in_loop:2, in_degree:2 |
| getFilesFromQCommandLineParser | (free) | linear_scan_in_loop:1, alloc_in_loop:1, in_degree:2 |
| loadImage_helper | (free) | complexity:7, lines:57, in_degree:2 |
| closeEvent | Page | complexity:10, cognitive:31 |
| creatGroup | PageScene | complexity:11, lines:63 |
| eventFilter | CCutWidget | complexity:8, cognitive:32 |
| eventType | CDrawToolEvent | complexity:14, cognitive:26 |
| getCenter | CGraphicsItem | complexity:11, cognitive:21 |
| getTransBlockFlag | CSizeHandleRect | complexity:11, cognitive:21 |
| getWHRadio | CGraphicsCutItem | complexity:9, cognitive:30 |
| itemChange | CGraphicsItem | complexity:8, cognitive:22 |
| operating | CGraphicsItem | complexity:8, cognitive:18 |
| registerAttributionWidgets | CPictureTool | complexity:10, lines:142 |
| sendToolEventToItem | CSelectTool | complexity:11, cognitive:20 |
| setCutType | CCutWidget | complexity:11, cognitive:20 |
| setSelectTextBlockAlign | CGraphicsTextItem | complexity:8, cognitive:15 |
| updateHandlesGeometry | CGraphicsCutItem | complexity:11, cognitive:30 |
| updateHandlesGeometry | CGraphicsPenItem | complexity:12, cognitive:33 |

## deepin-ocr — 3 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| setIcons | MainWidget | complexity:20, cognitive:38, lines:132, in_degree:1 |
| setupUi | MainWidget | complexity:13, cognitive:24, lines:264, in_degree:1 |
| pinchTriggered | ResultTextView | complexity:10, cognitive:19, in_degree:1 |

## deepin-picker — 2 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| ColorMenu | ColorMenu | complexity:8, cognitive:29, lines:102 |
| copyToClipboard | Clipboard | complexity:7, cognitive:28, lines:50 |

## deepin-terminal — 59 high 方法

| 方法 | 类 | 因子 |
|------|-----|------|
| createJsonGroup | MainWindow | complexity:17, cognitive:38, lines:94, loop_count:7, alloc_in_loop:4, in_degree:2 |
| parseExecutePara | Utils | complexity:10, cognitive:19, lines:104, linear_scan_in_loop:7, alloc_in_loop:1, in_degree:3 |
| removeTab | QTabBar | complexity:26, cognitive:89, lines:104, linear_scan_in_loop:3, in_degree:3, name_pattern:removeTab |
| createCustomCommandsFromConfig | ShortcutManager | complexity:10, cognitive:19, lines:68, linear_scan_in_loop:2, alloc_in_loop:1 |
| initServerConfig | ServerConfigManager | complexity:15, cognitive:25, lines:147, linear_scan_in_loop:8, alloc_in_loop:1 |
| readIniFunc | SettingIO | complexity:18, cognitive:70, lines:124, linear_scan_in_loop:4, alloc_in_loop:2 |
| writeIniFunc | SettingIO | complexity:11, cognitive:28, lines:86, alloc_in_loop:1, name_pattern:writeIniFunc |
| addMenuActions | TermWidget | complexity:14, cognitive:19, lines:101, in_degree:2 |
| clearChildrenFocus | Utils | linear_scan_in_loop:1, recursive, in_degree:2, name_pattern:clearChildrenFocus |
| compareWhiteList | FontFilter | complexity:6, lines:102, linear_scan_in_loop:2, alloc_in_loop:2 |
| escapedString | SettingIO | complexity:14, cognitive:34, lines:74, in_degree:2 |
| fillSearchPanel | ServerConfigManager | complexity:10, cognitive:29, lines:52, linear_scan_in_loop:7 |
| handleRightButtonClick | TabBar | complexity:5, lines:56, linear_scan_in_loop:1, in_degree:2 |
| iniUnescapedKey | SettingIO | complexity:8, cognitive:16, lines:66, linear_scan_in_loop:1 |
| init | Settings | complexity:11, cognitive:15, lines:199, in_degree:2 |
| notify | TerminalApplication | complexity:21, cognitive:44, lines:161, in_degree:2 |
| onServerConfigOptDlgFinished | ListView | complexity:7, cognitive:19, lines:92, in_degree:2 |
| setDeleteKey | RemoteManagementPlugin | complexity:5, cognitive:15, in_degree:2, name_pattern:setDeleteKey |
| setFocusFromeIndex | ListView | complexity:14, cognitive:41, lines:96, in_degree:3 |
| showHideOpacityAndBlurOptions | Service | complexity:15, cognitive:50, lines:86, in_degree:2 |
| slotAddSaveButtonClicked | ServerConfigOptDlg | complexity:11, cognitive:18, lines:83, name_pattern:slotAddSaveButtonClicked |
| slotAddSaveButtonClicked | CustomCommandOptDlg | complexity:11, cognitive:21, lines:87, name_pattern:slotAddSaveButtonClicked |
| unescapedString | SettingIO | complexity:14, cognitive:33, lines:118, in_degree:2 |
| GroupConfigOptDlg | GroupConfigOptDlg | complexity:9, lines:120, in_degree:40 |
| OnHandleCloseType | MainWindow | complexity:9, cognitive:15, in_degree:2 |
| TermWidget | TermWidget | complexity:13, cognitive:19, lines:161 |
| addThemeMenuItems | MainWindow | complexity:6, cognitive:17, lines:119 |
| canSplit | TermWidget | complexity:6, cognitive:16, linear_scan_in_loop:2 |
| checkShortcutValid | ShortcutManager | complexity:7, lines:61, in_degree:2 |
| closeEvent | MainWindow | complexity:6, lines:60, alloc_in_loop:1 |
| drawControl | TermTabStyle | complexity:7, cognitive:24, lines:70 |
| encodeList | Utils | lines:62, recursive, in_degree:4 |
| eventFilter | MainWindow | complexity:29, cognitive:86, lines:173 |
| eventFilter | QuakeWindow | complexity:12, cognitive:24, lines:78 |
| getConfigWindowState | MainWindow | complexity:5, cognitive:15, in_degree:2 |
| handleOSC52Clipboard | TermWidget | complexity:12, cognitive:20, lines:83 |
| initUI | RemoteManagementPanel | complexity:7, cognitive:15, lines:141 |
| loadConfiguration | CustomThemeSettingDialog | complexity:5, lines:70, in_degree:2 |
| menuHideSetThemeSlot | MainWindow | complexity:19, cognitive:126, lines:85 |
| onCustomCommandOptDlgFinished | ListView | complexity:7, lines:93, in_degree:2 |
| onSettingValueChanged | TermWidget | complexity:16, cognitive:20, lines:100 |
| paint | EncodeDelegate | complexity:5, lines:122, in_degree:6 |
| parseCommandLine | Utils | complexity:10, lines:100, in_degree:5 |
| quakeWindowShowOrHide | WindowsManager | complexity:6, lines:61, in_degree:2 |
| setAdvanceRegionVisible | ServerConfigOptDlg | complexity:11, cognitive:23, lines:80 |
| setBackspaceKey | RemoteManagementPlugin | complexity:5, cognitive:15, in_degree:2 |
| setSpaceInWord | Utils | complexity:8, cognitive:26, in_degree:5 |
| showGroupPanel | RemoteManagementTopPanel | complexity:6, lines:73, in_degree:2 |
| showPrevPanel | RemoteManagementTopPanel | complexity:18, cognitive:41, lines:138 |
| switchThemeAction | MainWindow | complexity:8, lines:58, in_degree:3 |
| main | (free) | complexity:7, lines:112, in_degree:4 |
| checkExtendThemeItem | MainWindow | complexity:12, cognitive:78 |
| eventFilter | TabBar | complexity:8, cognitive:23 |
| keyPressEvent | EncodeListView | complexity:8, cognitive:17 |
| onTermWidgetReceivedData | TermWidget | complexity:8, lines:50 |
| setThemeCheckItemSlot | MainWindow | complexity:9, lines:89 |
| showHideDebuginfodUrlsOptions | Service | complexity:8, cognitive:21 |
| showSearchBar | TermWidgetPage | complexity:8, cognitive:18 |
| variantToString | SettingIO | complexity:11, cognitive:22 |
