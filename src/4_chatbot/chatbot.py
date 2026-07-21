"""
chatbot.py
----------
STEP 4b – The GraphRAG Chatbot (command-line interface).

This is the interactive chatbot that uses graphrag_retriever.py
to answer questions about the power plant.

HOW TO RUN:
  python src/4_chatbot/chatbot.py

To run as a simple web API instead, use:
  python src/4_chatbot/chatbot.py --server
"""

import os
import sys
import json
import argparse
from graphrag_retriever import GraphRAGRetriever, call_llm_bedrock, call_llm_openai, LLM_PROVIDER

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║         ⚡  Power Plant GraphRAG Chatbot  ⚡                 ║
║  Ask anything about the diesel power plant components,       ║
║  fault codes, maintenance, or operating parameters.          ║
║  Type 'exit' or 'quit' to stop.  Type 'debug' to toggle     ║
║  verbose retrieval info.                                     ║
╚══════════════════════════════════════════════════════════════╝
"""

EXAMPLE_QUESTIONS = [
    "What happens when fault F-002 is triggered?",
    "Explain the relationship between the governor and the diesel engine.",
    "What are the maintenance intervals for PP-ENG-001?",
    "What is the normal operating voltage range for the generator?",
    "If the AVR fails, what components are affected?",
    "List all components connected to the main bus bar.",
    "What should I do if the Buchholz relay trips?",
]


def answer_question(retriever: GraphRAGRetriever, question: str, show_debug: bool = False) -> str:
    """Run the full GraphRAG pipeline for one question."""
    # Retrieve context (text chunks + graph context)
    context, debug = retriever.retrieve(question)

    if show_debug:
        print("\n── DEBUG ──────────────────────────────────────────────────")
        print(json.dumps(debug, indent=2))
        print("── CONTEXT SENT TO LLM ────────────────────────────────────")
        print(context[:2000] + ("..." if len(context) > 2000 else ""))
        print("───────────────────────────────────────────────────────────\n")

    # Call LLM
    if LLM_PROVIDER == "openai":
        answer = call_llm_openai(question, context)
    else:
        answer = call_llm_bedrock(question, context)

    return answer


def run_cli():
    """Interactive command-line chatbot."""
    print(BANNER)
    print("💡 Example questions:")
    for i, q in enumerate(EXAMPLE_QUESTIONS, 1):
        print(f"   {i}. {q}")
    print()

    retriever  = GraphRAGRetriever()
    show_debug = False

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 Goodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit", "q"):
                print("👋 Goodbye!")
                break

            if user_input.lower() == "debug":
                show_debug = not show_debug
                print(f"🔧 Debug mode: {'ON' if show_debug else 'OFF'}")
                continue

            if user_input.lower() == "examples":
                for i, q in enumerate(EXAMPLE_QUESTIONS, 1):
                    print(f"  {i}. {q}")
                continue

            print("\n🤔 Thinking...\n")
            try:
                answer = answer_question(retriever, user_input, show_debug)
                print(f"Bot: {answer}\n")
                print("─" * 60)
            except Exception as e:
                print(f"❌ Error: {e}\n")

    finally:
        retriever.close()


def run_server():
    """
    Simple HTTP API server using Flask.
    Exposes POST /ask  { "question": "..." } → { "answer": "...", "debug": {...} }

    WHY AN API?
    This lets you connect a frontend (React, Slack bot, etc.) to the chatbot.
    """
    try:
        from flask import Flask, request, jsonify
        from flask_cors import CORS
    except ImportError:
        print("❌ Flask not installed. Run: pip install flask flask-cors")
        sys.exit(1)

    app     = Flask(__name__)
    CORS(app)   # Allow cross-origin requests from a frontend
    retriever = GraphRAGRetriever()

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({"status": "ok", "service": "graphrag-chatbot"})

    @app.route("/ask", methods=["POST"])
    def ask():
        data     = request.get_json()
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "question field is required"}), 400

        context, debug = retriever.retrieve(question)

        if LLM_PROVIDER == "openai":
            answer = call_llm_openai(question, context)
        else:
            answer = call_llm_bedrock(question, context)

        return jsonify({"answer": answer, "debug": debug})

    port = int(os.getenv("CHATBOT_PORT", "8080"))
    print(f"🚀 GraphRAG API server starting on http://0.0.0.0:{port}")
    print(f"   POST /ask  {{ \"question\": \"...\" }}")
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Power Plant GraphRAG Chatbot")
    parser.add_argument("--server", action="store_true", help="Run as HTTP API server")
    args = parser.parse_args()

    if args.server:
        run_server()
    else:
        run_cli()
