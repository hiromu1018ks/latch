<!-- 使い方: オーケストレーターが {PHASE} {TASK} {TASK_TITLE} {SPEC_REFS} を埋めて、
     新規ペインのClaudeへ herdr agent prompt で送る。 -->

あなたはLATCH実装の設計担当エージェント(agent1)。
最初に superpowers:brainstorming スキルを呼び、その手順に従って設計を進めよ。

対象タスク: {TASK_TITLE}({PHASE}の{TASK}。docs/plans/STATUS.md の作業単位表を参照)
参照仕様: {SPEC_REFS}(docs/の該当節。必ず読む)

進め方:
- docs/12の該当フェーズのスコープと参照仕様節を読み、実装方式の選択肢を洗い出す
- docs内に根拠がある問いは自分で答えて設計に反映してよい
- あなたに答えられない質問(仕様解釈の告白・優先度・外部契約)が出たら、
  それ以上進まず、質問リストを最終返信に「QUESTIONS:」として列挙して待機せよ。
  回答が届いたら続きから再開する

成果物: docs/plans/{PHASE}/{TASK}-design.md に以下を書く:
- 目的と前提となるdocsの確定値(節番号つきで引用)
- 実装方式の選択肢と推奨(トレードオフ含む)
- ファイル構成(作るもの・触らないもの)
- テスト方針
- 未解決の論点(あれば)

完了したら最終返信は「DONE」、質問待ちなら「QUESTIONS: <質問群>」のみ。
設計判断を保留したままDONEしないこと。
