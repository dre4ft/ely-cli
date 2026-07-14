# Ely — Rust Port

## Install

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Build
cd ely_rust
cargo build --release
```

## Usage

```bash
./target/release/ely                      # REPL
./target/release/ely "explique asyncio"   # Single-shot
./target/release/ely --context code       # Contexte
./target/release/ely --pro                # Provider pro
./target/release/ely --config ./prod.yaml # Config custom
```

## Structure

```
src/
├── main.rs         # Entry point
├── config.rs       # YAML config
├── providers.rs    # OpenAI/Ollama/LM Studio
├── agent.rs        # Function-calling loop
├── tools.rs        # bash, read_file, write_file, grep...
├── skills.rs       # Skills system
├── contexts.rs     # Context management
├── memory.rs       # Memory compaction
├── subagent.rs     # Background tasks
├── mcp.rs          # MCP client
├── guard.rs        # Prompt injection
├── prompts.rs      # Templates
└── cli.rs          # REPL, /commands, #bash
```
