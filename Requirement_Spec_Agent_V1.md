Technical Requirement: AI-Driven Automation Agent
1. Project Overview
Develop a Portable EXE that integrates a Chatbot (using Phi-3.5 Mini) with a Web Automation Engine (Playwright). The system allows users to execute web workflows through natural language commands and provides automated verification via database snapshots.

2. Core Feature: The "Commander" Chatbot
The interface features a chat input for user "Intents." The AI (Child Bot) parses these instructions to trigger the appropriate automated flow.

A. Intent Execution
Command Example: "Create 6 enrollments."

Logic: The bot identifies the requested task ("Enrollment") and the count ("6"), then triggers the automation loop to repeat the process.

Flexibility: The chatbot must map various natural language phrases to the same underlying automated task.

B. Custom Data Integration
Command Example: "Create customers using these specific names and SSNs: [Input Data]."

Logic: If the user provides specific values in the chat, the system must use them exactly. Any other required fields in the web form are auto-filled by the AI to ensure completion.

3. Automation & "Teaching" Mode
The Walk-Around: A recording mode where the agent observes a user navigating a website once to learn the steps.

Semantic Capture: The agent identifies buttons and fields by their labels and roles (e.g., "First Name" or "Submit") to ensure the script remains stable even if the website layout changes.

Execution Engine: Uses these recorded steps to perform the tasks requested via the Chatbot at high speed.

4. Verification & Reporting
Database Validation: After the web task is finished, the agent automatically queries the target database to confirm the record was created.

Proof of Work: The tool generates a report containing:

A screenshot of the Web UI "Success" page.

The corresponding record retrieved from the Database.

A simple "Match" confirmation.

5. Portability & Performance
Optimization: Designed to run on 8GB RAM and Intel i5 hardware.

Model: Phi-3.5 Mini (Local GGUF).

Security: The Python code is compiled into a protected binary (using Nuitka) to prevent local modification of the execution logic.