# Application Concept: The Rigorous AI Grader and Rubric Validator

## 1. Problem and market need

University teaching assistants and professors spend countless hours grading multi-task assignments. Verifying that a submission meets complex rubric criteria while also enforcing strict formatting and validation standards (specific Markdown conventions, file naming rules) is tedious and prone to human error. Different graders also apply the same rubric differently. This application automates the evaluation and applies the same standard every time.

## 2. Application design and interaction flow

**Inputs.** The UI has three text areas: the assignment rubric, the formatting and validation rules, and the student's submission text. A fourth, optional field takes the submission's file name, because file-naming rules cannot be checked from pasted text alone.

**Role of the generative API.** The model acts as an automated teaching assistant. It checks the submission against the formatting rules, grades each task against the rubric with a structured verification protocol, and writes the final report. Totals are computed in code, not by the model.

**Output.** A structured Markdown report with the grade out of 100, itemized feedback for every task in the rubric (however many there are), and a list of every formatting penalty with its evidence. The report can be downloaded.

**Flow.**

1. The user pastes the rubric, rules and submission, and clicks **Grade Submission**.
2. Prompt 1 audits the formatting rules.
3. Prompt 2 grades the content, using Prompt 1's output as input, and runs several independent times.
4. Code merges the passes, scales to 100, applies penalties and floors the result at zero.
5. Prompt 3 writes the report from the verified numbers, and the app checks that the report kept them.
6. The UI shows score metrics, the rendered report, and an expandable view of the prompt chain.

## 3. Prompt engineering techniques

**Persona pattern.** The system prompt puts the model in a specific role to prevent lenient or conversational grading:

> Act as a rigorous, uncompromising university grader. You must triple-verify all answers against the provided rubric and ruthlessly enforce the formatting standards without exception. Do not provide hints or conversational filler.

The same prompt also marks the submission as untrusted data, so instructions hidden in a submission are ignored and flagged.

**Iterative Refinement pattern.** Instead of asking for everything at once, the backend chains prompts:

- Prompt 1 strictly checks the submission against the formatting rules.
- Prompt 2 receives Prompt 1's output and evaluates the academic content against the rubric. It is told to locate, compare and re-verify each task, and it runs three independent times so disagreement can be detected.
- Prompt 3 compiles the verified findings into the final report. The score arithmetic is done in code beforehand, and the app falls back to a code-built report if the model changes any number.

## 4. User interface and publishing

A clean web app built with Python and Streamlit, with text areas for the inputs, a settings sidebar (provider, model, verification passes, penalty defaults) and an output pane that renders the report's Markdown tables. The code lives in a public GitHub repository. For the publishing bonus, deploy to **Streamlit Community Cloud**, which hosts Streamlit apps directly from a GitHub repository and gives a public URL. (Vercel is not suitable for Streamlit apps, because Streamlit needs a long-running server.)

## 5. Impact and future applications

**Impact.** Faster grading, consistent application of the rubric, and visible evidence for every deduction, which makes grade disputes easier to resolve. TAs spend less time on mechanical checks and more time teaching.

**Future applications.** Batch grading of a whole class, LMS integration, an instructor review screen for adjusting scores before release, few-shot examples from past graded work to calibrate to a course, and the same engine for code-style linting, lab reports or résumé screening against a checklist.

## 6. Responsible use

AI grades can be wrong. The tool is a first-pass assistant, and an instructor should review each grade before release. Student data should not be pasted into a public deployment without institutional approval.
