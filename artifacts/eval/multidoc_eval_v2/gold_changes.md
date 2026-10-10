# Eval v2 Gold 修改与人工审阅说明

逐项阅读冻结 corpus 的 43 个完整 chunk 和 v1 的 29 组 Top-5 后制定；这是可人工审查的标注，不声称已经由业务负责人签字。
本文件的映射只用于计分，retriever 不接收这些字段。任何后续标注修订必须另存实验版本，不回改 v1。

S04 的返回和传送按源手册明确能到社交岛的目的等价处理；preferred 保留返回按钮，不合并操作路径。

## E01 岳阳医院场景总共有几层？

- Required facts：医院元宇宙场景总共5层空间。
- Old Gold：hospital-scene-guide#4 (`3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4`)
- Acceptable：hospital-scene-guide#4 (`3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4`), hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- Preferred：hospital-scene-guide#4 (`3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4`)
- 理由：布局块直接声明总层数；五楼块内独立 FAQ 也明确回答总共5层，并非根据五楼名称推断。

  - hospital-scene-guide#4：直接写明总共5层。
  - hospital-scene-guide#9：数据中心说明与两个 FAQ 保留在同一块；涉及层数时以明确总数 FAQ 为依据。

## E02 二楼问诊中心右侧有哪些诊疗室？

- Required facts：二楼问诊中心右侧包括物理治疗、作业治疗、吞咽康复、言语康复诊疗室。
- Old Gold：hospital-scene-guide#6 (`9eb6453c-4748-582f-9775-78dcbc68476d`)
- Acceptable：hospital-scene-guide#6 (`9eb6453c-4748-582f-9775-78dcbc68476d`)
- Preferred：hospital-scene-guide#6 (`9eb6453c-4748-582f-9775-78dcbc68476d`)
- 理由：只有二楼段落独立列出右侧房间；楼层总览不足以支持房间清单。

  - hospital-scene-guide#6：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## E03 智能步道康复室在哪一层？

- Required facts：智能步道康复室位于三楼诊疗中心。
- Old Gold：hospital-scene-guide#7 (`0f80e400-75cd-5b74-85eb-dcbf125f8aa4`)
- Acceptable：hospital-scene-guide#7 (`0f80e400-75cd-5b74-85eb-dcbf125f8aa4`)
- Preferred：hospital-scene-guide#7 (`0f80e400-75cd-5b74-85eb-dcbf125f8aa4`)
- 理由：三楼段落同时给出楼层和智能步道康复室名称。

  - hospital-scene-guide#7：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## E04 四楼能做哪些类型的评估？

- Required facts：四楼评估中心提供日常生活活动、感觉、运动、认知、言语语言、心理情绪、生活质量、社会功能评估。
- Old Gold：hospital-scene-guide#8 (`8c7a2240-fbed-5180-95bb-0399a1d4de0f`)
- Acceptable：hospital-scene-guide#8 (`8c7a2240-fbed-5180-95bb-0399a1d4de0f`)
- Preferred：hospital-scene-guide#8 (`8c7a2240-fbed-5180-95bb-0399a1d4de0f`)
- 理由：四楼段落完整列出评估类型；泛称评估中心不充分。

  - hospital-scene-guide#8：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## E05 五楼数据中心的大屏会展示哪些数据？

- Required facts：五楼数据中心大屏展示患者流量、病例病案、诊疗数据、健康报告等实时信息。
- Old Gold：hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- Acceptable：hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- Preferred：hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- 理由：五楼段落包含地点及数据类别。

  - hospital-scene-guide#9：数据中心说明与两个 FAQ 保留在同一块；涉及层数时以明确总数 FAQ 为依据。

## E06 岳阳医院一楼主要是什么区域？

- Required facts：岳阳医院场景一楼为接待大厅。
- Old Gold：hospital-scene-guide#5 (`864f3f41-85ce-5a85-9b81-f0baa812880a`)
- Acceptable：hospital-scene-guide#5 (`864f3f41-85ce-5a85-9b81-f0baa812880a`)
- Preferred：hospital-scene-guide#5 (`864f3f41-85ce-5a85-9b81-f0baa812880a`)
- 理由：一楼标题直接绑定接待大厅；五楼 FAQ 的功能列表未显式逐一绑定楼层，保守不扩充。

  - hospital-scene-guide#5：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## S01 我想做记忆和认知方面的评估，应该去哪里？

- Required facts：可到四楼评估中心进行认知功能或认知评估。
- Old Gold：hospital-scene-guide#8 (`8c7a2240-fbed-5180-95bb-0399a1d4de0f`)
- Acceptable：hospital-scene-guide#8 (`8c7a2240-fbed-5180-95bb-0399a1d4de0f`)
- Preferred：hospital-scene-guide#8 (`8c7a2240-fbed-5180-95bb-0399a1d4de0f`)
- 理由：四楼段落有认知功能和认知评估室；不额外声称未写明的临床服务。

  - hospital-scene-guide#8：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## S02 我想练智能步道，应该去几楼找？

- Required facts：到三楼诊疗中心找智能步道康复室。
- Old Gold：hospital-scene-guide#7 (`0f80e400-75cd-5b74-85eb-dcbf125f8aa4`)
- Acceptable：hospital-scene-guide#7 (`0f80e400-75cd-5b74-85eb-dcbf125f8aa4`)
- Preferred：hospital-scene-guide#7 (`0f80e400-75cd-5b74-85eb-dcbf125f8aa4`)
- 理由：三楼段落直接给出设施与楼层。

  - hospital-scene-guide#7：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## S03 我想看患者流量、病例和诊疗数据，应该看哪一层的大屏？

- Required facts：患者流量、病例和诊疗数据可在五楼数据中心大屏查看。
- Old Gold：hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- Acceptable：hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- Preferred：hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- 理由：数据类别与五楼在同一 chunk 中明确对应。

  - hospital-scene-guide#9：数据中心说明与两个 FAQ 保留在同一块；涉及层数时以明确总数 FAQ 为依据。

## S04 我怎样从岳阳医院场景回到社交岛？

- Required facts：给出一条从医院场景到社交岛的可执行路径：返回按钮、明确去社交岛的场景传送或快速通道入口，任一路径即可。
- Old Gold：hospital-scene-guide#2 (`6e7a371a-e8d0-55d7-8e75-0cf7916f0c2c`)
- Acceptable：hospital-scene-guide#2 (`6e7a371a-e8d0-55d7-8e75-0cf7916f0c2c`), yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`), yueyang-patient-guide#6 (`7310de2e-c2fd-577e-af0d-f8efa581fe55`), yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`), yueyang-doctor-guide#4 (`8906e256-11d8-5b5f-9689-40c12f3bb50f`), yueyang-doctor-guide#6 (`c9bc6209-00dc-5495-b709-d316a829c572`), yueyang-doctor-guide#9 (`6f2f77e5-9dfd-5466-a5c5-f05501468c01`)
- Preferred：hospital-scene-guide#2 (`6e7a371a-e8d0-55d7-8e75-0cf7916f0c2c`)
- 理由：医院角色手册明确给出去社交岛的场景传送与快速通道入口，能达到同一目的；preferred 保留医院返回按钮以区分返回与通用传送。不接受只有社交岛标题的块。

  - hospital-scene-guide#2：独立描述设置旁的返回按钮及返回社交岛。
  - yueyang-patient-guide#6：场景传送步骤和带我去社交岛指令均明确。
  - yueyang-doctor-guide#6：场景传送步骤和带我去社交岛指令均明确。
  - yueyang-patient-guide#10：患者快速通道完整列出入口，且包含去社交岛的操作/FAQ。
  - yueyang-doctor-guide#9：医生快速通道完整列出入口，明确快速就诊前往问诊室，包含社交岛操作。
  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。
  - yueyang-doctor-guide#4：医生按钮详细介绍包含设备管理和医生快速通道具体入口。

## P01 患者端右上角的两个按钮是什么？

- Required facts：患者端右上角从上到下为立即挂号、训练提醒。
- Old Gold：yueyang-patient-guide#3 (`8e686d39-0e33-5fa0-9710-b86468cf62d9`)
- Acceptable：yueyang-patient-guide#3 (`8e686d39-0e33-5fa0-9710-b86468cf62d9`), yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`)
- Preferred：yueyang-patient-guide#3 (`8e686d39-0e33-5fa0-9710-b86468cf62d9`)
- 理由：按键分布和按钮详细介绍中的 FAQ 均独立明确右上角位置及两个按钮；preferred 是直接布局段。

  - yueyang-patient-guide#3：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。
  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。

## P02 患者在哪里查看当前训练安排？

- Required facts：患者通过训练提醒查看当前训练安排；入口在右上角。
- Old Gold：yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`)
- Acceptable：yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`)
- Preferred：yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`)
- 理由：详细介绍明确当前训练安排与入口；训练报告属于另一意图，按键分布仅有名称没有安排功能，均不扩为 acceptable。

  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。

## P03 患者端快速通道里面有哪些入口？

- Required facts：患者快速通道含快速就诊、快速测评、去社交岛、体验小游戏。
- Old Gold：yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`)
- Acceptable：yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`), yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`)
- Preferred：yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`)
- 理由：专项快速通道及按钮详细介绍各自完整列出四个入口；医生列表缺少患者快速测评，不可接受。

  - yueyang-patient-guide#10：患者快速通道完整列出入口，且包含去社交岛的操作/FAQ。
  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。

## P04 患者怎么手动挂号？

- Required facts：通过立即挂号入口打开挂号窗口并选择在线医生进行手动挂号。
- Old Gold：yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- Acceptable：yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`), yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- Preferred：yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- 理由：专项手动步骤最直接；按钮介绍独立说明立即挂号窗口及在线医生选择。通用患者语音冲突段未提供手动步骤，不接受。

  - yueyang-patient-guide#9：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。
  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。

## D01 医生在哪里查看和管理设备？

- Required facts：医生可通过设备管理入口查看和管理设备。
- Old Gold：yueyang-doctor-guide#4 (`8906e256-11d8-5b5f-9689-40c12f3bb50f`)
- Acceptable：yueyang-doctor-guide#4 (`8906e256-11d8-5b5f-9689-40c12f3bb50f`)
- Preferred：yueyang-doctor-guide#4 (`8906e256-11d8-5b5f-9689-40c12f3bb50f`)
- 理由：设备管理说明包含入口及功能；只描述医生问诊不相关。

  - yueyang-doctor-guide#4：医生按钮详细介绍包含设备管理和医生快速通道具体入口。

## D02 医生端快速通道包含哪些功能？

- Required facts：医生快速通道包括医生快速就诊、去社交岛、体验小游戏。
- Old Gold：yueyang-doctor-guide#9 (`6f2f77e5-9dfd-5466-a5c5-f05501468c01`)
- Acceptable：yueyang-doctor-guide#4 (`8906e256-11d8-5b5f-9689-40c12f3bb50f`), yueyang-doctor-guide#9 (`6f2f77e5-9dfd-5466-a5c5-f05501468c01`)
- Preferred：yueyang-doctor-guide#9 (`6f2f77e5-9dfd-5466-a5c5-f05501468c01`)
- 理由：专项段与按钮介绍分别完整列出医生功能；患者列表含不同角色项目，不替代医生证据。

  - yueyang-doctor-guide#9：医生快速通道完整列出入口，明确快速就诊前往问诊室，包含社交岛操作。
  - yueyang-doctor-guide#4：医生按钮详细介绍包含设备管理和医生快速通道具体入口。

## D03 医生怎样快速进入问诊？

- Required facts：给出医生快速进入问诊的明确操作路径，个人中心立即前往问诊或快速通道医生快速就诊均可。
- Old Gold：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- Acceptable：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`), yueyang-doctor-guide#9 (`6f2f77e5-9dfd-5466-a5c5-f05501468c01`)
- Preferred：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- 理由：通用医生给出个人中心路径；岳阳医生专项明确快速通道步骤及前往问诊室。按钮列表只命名快速就诊而没有绑定问诊室，保守不扩充。

  - general-doctor-guide#2：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。
  - yueyang-doctor-guide#9：医生快速通道完整列出入口，明确快速就诊前往问诊室，包含社交岛操作。

## D04 医生能不能直接用语音进入问诊？

- Required facts：医生可以用语音指令立即前往问诊进入问诊。
- Old Gold：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- Acceptable：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- Preferred：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- 理由：只有通用医生段落明确该语音指令；手动快速通道不能推断支持语音。

  - general-doctor-guide#2：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## R01 我是患者，怎么查看自己的就诊记录和训练报告？

- Required facts：患者可在个人中心的就诊记录查看记录与报告，也可使用文档提供的查看病历报告语音入口。
- Old Gold：general-patient-guide#3 (`a64d2ec7-b690-5968-9d36-9ed3eb08c9e6`)
- Acceptable：general-patient-guide#3 (`a64d2ec7-b690-5968-9d36-9ed3eb08c9e6`)
- Preferred：general-patient-guide#3 (`a64d2ec7-b690-5968-9d36-9ed3eb08c9e6`)
- 理由：该段给出记录报告入口且明确患者角色；查看患者信息与训练安排不满足此事实。

  - general-patient-guide#3：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## R02 我是医生，为什么看不到患者端的立即挂号？

- Required facts：立即挂号是患者角色功能，医生角色无法查看该选项。
- Old Gold：yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- Acceptable：yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- Preferred：yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- 理由：专项段同时给出立即挂号和仅患者/医生不可见限制；仅标患者按钮但没有明确限制的介绍不扩充。目标角色 patient，不是请求者 doctor。

  - yueyang-patient-guide#9：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## R03 我是患者，能使用“立即前往问诊”这个医生入口吗？

- Required facts：立即前往问诊入口仅限医生，患者角色无法查看。
- Old Gold：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- Acceptable：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- Preferred：general-doctor-guide#2 (`a341b833-dbe6-5b85-a717-5ac916aaa0c8`)
- 理由：通用医生段明确命名入口及患者不可见；模糊的角色不同说明不足。目标角色 doctor，不是请求者 patient。

  - general-doctor-guide#2：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## R04 我是患者，想快速就诊，应该从哪里进入？

- Required facts：患者可从快速通道中的患者端快速就诊入口进入。
- Old Gold：yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`)
- Acceptable：yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`), yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`)
- Preferred：yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`)
- 理由：两个患者段独立给出快速通道及患者快速就诊；医生个人中心入口不可替代。

  - yueyang-patient-guide#10：患者快速通道完整列出入口，且包含去社交岛的操作/FAQ。
  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。

## M01 怎么去社交岛？

- Required facts：提供一条明确到社交岛的操作路径，返回、场景传送或快速通道任选一种。
- Old Gold：hospital-scene-guide#2 (`6e7a371a-e8d0-55d7-8e75-0cf7916f0c2c`), yueyang-patient-guide#6 (`7310de2e-c2fd-577e-af0d-f8efa581fe55`), yueyang-doctor-guide#6 (`c9bc6209-00dc-5495-b709-d316a829c572`)
- Acceptable：hospital-scene-guide#2 (`6e7a371a-e8d0-55d7-8e75-0cf7916f0c2c`), yueyang-patient-guide#4 (`e8ae4d2d-829b-5904-946a-add5da0a49ec`), yueyang-patient-guide#6 (`7310de2e-c2fd-577e-af0d-f8efa581fe55`), yueyang-patient-guide#10 (`09fc5cf6-ba68-5f3f-af17-266420bc254a`), yueyang-doctor-guide#4 (`8906e256-11d8-5b5f-9689-40c12f3bb50f`), yueyang-doctor-guide#6 (`c9bc6209-00dc-5495-b709-d316a829c572`), yueyang-doctor-guide#9 (`6f2f77e5-9dfd-5466-a5c5-f05501468c01`)
- Preferred：hospital-scene-guide#2 (`6e7a371a-e8d0-55d7-8e75-0cf7916f0c2c`), yueyang-patient-guide#6 (`7310de2e-c2fd-577e-af0d-f8efa581fe55`), yueyang-doctor-guide#6 (`c9bc6209-00dc-5495-b709-d316a829c572`)
- 理由：返回、两份场景传送及两份快速通道/按钮介绍均有可执行入口；单纯社交岛标题不支持操作。preferred 保留独立直接操作段，非权威来源优先级。

  - hospital-scene-guide#2：独立描述设置旁的返回按钮及返回社交岛。
  - yueyang-patient-guide#6：场景传送步骤和带我去社交岛指令均明确。
  - yueyang-doctor-guide#6：场景传送步骤和带我去社交岛指令均明确。
  - yueyang-patient-guide#10：患者快速通道完整列出入口，且包含去社交岛的操作/FAQ。
  - yueyang-doctor-guide#9：医生快速通道完整列出入口，明确快速就诊前往问诊室，包含社交岛操作。
  - yueyang-patient-guide#4：患者按钮详细介绍包含相关功能的具体说明及 FAQ；每个 case 仅采纳其明确提供的事实。
  - yueyang-doctor-guide#4：医生按钮详细介绍包含设备管理和医生快速通道具体入口。

## M02 怎么去二楼？

- Required facts：使用楼层传送选择二楼确认，或使用带我去二楼指令。
- Old Gold：yueyang-patient-guide#7 (`11ce42dd-1c7e-546a-9acd-39d6a79bb35a`), yueyang-doctor-guide#7 (`130f4d00-1032-5988-8044-78738c4b86f7`)
- Acceptable：yueyang-patient-guide#7 (`11ce42dd-1c7e-546a-9acd-39d6a79bb35a`), yueyang-doctor-guide#7 (`130f4d00-1032-5988-8044-78738c4b86f7`)
- Preferred：yueyang-patient-guide#7 (`11ce42dd-1c7e-546a-9acd-39d6a79bb35a`), yueyang-doctor-guide#7 (`130f4d00-1032-5988-8044-78738c4b86f7`)
- 理由：两份楼层操作段独立给出步骤和二楼指令；二楼房间内容不能回答如何前往。

  - yueyang-patient-guide#7：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。
  - yueyang-doctor-guide#7：本块的标题和正文共同明确提供该 case 的 required_facts；逐块复核未依赖其他块补全。

## B01 岳阳医院有六楼吗？

- Required facts：医院场景总共5层，因此没有第六层。
- Old Gold：hospital-scene-guide#4 (`3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4`)
- Acceptable：hospital-scene-guide#4 (`3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4`), hospital-scene-guide#9 (`43717f17-d92a-5d01-b3cb-a8237bfb002f`)
- Preferred：hospital-scene-guide#4 (`3088b1e7-fa9c-56f5-9306-31bc5bd5d1f4`)
- 理由：总共5层的布局声明与 FAQ 均可独立证明边界；仅出现五楼名称不够。

  - hospital-scene-guide#4：直接写明总共5层。
  - hospital-scene-guide#9：数据中心说明与两个 FAQ 保留在同一块；涉及层数时以明确总数 FAQ 为依据。

## N01 怎么修改登录密码？

- Required facts：需要登录密码修改入口及步骤，现有语料未提供。
- Old Gold：无
- Acceptable：无
- Preferred：无
- 理由：返回登录界面不是修改密码；没有单块提供密码修改事实。


## N02 岳阳医院停车位怎么预约？

- Required facts：需要停车位预约入口及步骤，现有语料未提供。
- Old Gold：无
- Acceptable：无
- Preferred：无
- 理由：就诊挂号不等于停车位预约；医院场景总览也没有停车服务。


## N03 怎么把训练报告导出成 PDF？

- Required facts：需要训练报告 PDF 导出方法，现有语料未提供。
- Old Gold：无
- Acceptable：无
- Preferred：无
- 理由：查看报告不等于 PDF 导出，不接受用报告查看证据补造导出能力。


## C01 患者端用语音挂号时应该说什么？

- Required facts：暴露两份患者手册关于语音挂号口令的冲突，不能由检索器选定正确口令。
- Old Gold：general-patient-guide#4 (`bdf6c420-0cdb-55a7-8318-879691327759`), yueyang-patient-guide#9 (`367a260b-fd6a-5a92-a436-d156fbccc1c6`)
- Acceptable：无
- Preferred：无
- 理由：以两个冲突来源组计算 ConflictRecall；不存在单个可裁定正确口令的 acceptable 答案块，不进入传统质量指标。

- 冲突组：yueyang-patient-guide#9 与 general-patient-guide#4，保持双方，不判断权威。
