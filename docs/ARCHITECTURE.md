# Studyroom architecture

The [agent flow in the README](../README.md#agent-flow) shows the main handoffs. The Python backend coordinates preparation and question generation. The orchestrator coordinates live tutoring and assessment.

## Preparation and questions

1. **Prepare the material.** Python extracts text from uploaded PDF, TXT or Markdown files. For a topic request, the Knowledge workflow interprets the description and asks the student to choose a meaning if it is ambiguous.
2. **Find sources.** The Research Agent can call `web_search` and receives the fetched passages as tool results. Set `WEB_SEARCH_ON_UPLOAD=false` to skip automatic research for uploads. Topic-only preparation needs usable web sources.
3. **Build the learning map.** The Knowledge Agent proposes concepts and prerequisites. Python discards unsupported quotes and filters invalid or cyclic edges. Quotes are optional.
4. **Open the study chat.** Preparation status is recorded in SQLite and shown as a short progress flow. A completed preparation opens one chat, even if opening is retried. Reopen saved chats from the sidebar. To retry interrupted preparation, reselect the files or retry the topic.
5. **Plan and ask.** The Planner builds the learning plan. Python selects an unlocked concept, and the Question Generator uses that concept, the plan, sources and available hypotheses to write a text question. The reference answer and rubric stay private.

## Live study

- **Workspace activity.** The browser saves draft edits and sends idle signals for a visible, unanswered question. A stable draft can trigger tentative Hypothesis analysis. Draft hypotheses stay in the session.
- **Help.** The Observer decides **WAIT** or **WAKE** after an editing pause, inactivity or an incorrect assessment. Routine reviews are spaced by `OBSERVER_REVIEW_SECONDS`, and proactive help can be disabled. An explicit help request or WAKE goes to the Planner, then the Tutor. The Planner honors a named tool and avoids repeating an automatic hint on the same question. The Tutor returns a validated hint, explanation, text highlight, guiding question or React Flow slideshow.
- **Submit.** Assessment grades the exact submitted text and saves the result and mastery update first. Reflection updates the concept summary and next goal. Hypothesis analysis uses the submission, assessment, available Reflection and prior evidence to identify possible gaps. The browser normally receives the grade after those follow-up steps finish. A failure in either follow-up leaves the saved grade available.
- **Continue.** A correct answer advances the study. An incorrect answer keeps the current question and can trigger an Observer review. The Question Generator can use the assessed hypotheses for later questions.

## Context and memory

[The context builder](../backend/context.py) gives agents the current question, draft, active plan, relevant history, source passages and scoped learning memory. It trims older history and lower-ranked passages to fit the input budget. State revisions help the backend discard stale results.

- **Session memory** stores the current question, saved draft, plan, assessment and hypotheses for the chat.
- **Long-term memory** keeps mastery estimates and one rolling summary and next goal per concept, plus student goals and a short learner profile. Reflection reads the latest corrected summary before updating it. The Planner can use concept memory across the material's map. Other study agents receive concept memory for the current concept and its direct prerequisites. Preparation receives student goals, the learner profile and known concepts with their mastery estimates. Archived notes are visible in the Memory tab and excluded from model context.
- **Corrections** let students edit summaries, goals and their learner profile, or dispute possible misconceptions. The app keeps the original evidence and correction history.

Assessment scores are provisional and update mastery through Bayesian Knowledge Tracing. Hypotheses are tentative and link to evidence. [Workflow tracing](workflow.md) explains how these steps are recorded and delivered.
