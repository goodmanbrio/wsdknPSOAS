# PMS1 + PTECA + stencil2chart as Harness Tools

Design brief for wrapping the PMS1 pipeline, PTECA, and stencil2chart
as tools inside a single top-level LLM agent loop (Pattern A / Claude
Code style).

---

## Ground up: what is a tool-use loop

### Step 0: Normal LLM call (no tools)

```python
import anthropic
client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": "What is 2+2?"}],
)

print(response.content)
# [TextBlock(text="4")]
```

You send messages. LLM returns text. Done. One shot.

### Step 1: Tell the LLM "you have tools"

A tool definition is just a JSON schema describing a function the LLM
is **allowed to ask you to call.** The LLM never calls it itself — it
says "I want to call this function with these arguments" and YOU
execute it.

```python
tools = [
    {
        "name": "add_numbers",
        "description": "Add two numbers together",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    }
]

response = client.messages.create(
    model="claude-opus-4-8",
    messages=[{"role": "user", "content": "What is 2+2?"}],
    tools=tools,  # "hey LLM, you can ask me to call this"
)
```

### Step 2: What comes back

The response has a `stop_reason`. Two possible values matter:

```
stop_reason = "end_turn"    -> LLM is done, gave you text
stop_reason = "tool_use"    -> LLM wants you to call a tool
```

When `stop_reason = "tool_use"`, the response content looks like:

```python
response.content = [
    ToolUseBlock(
        id="toolu_abc123",          # unique ID for this tool call
        name="add_numbers",          # which tool
        input={"a": 2, "b": 2},     # arguments the LLM chose
    )
]
```

**The LLM did NOT execute anything.** It said: "please call
`add_numbers(a=2, b=2)` for me and tell me what happened."

### Step 3: You execute the tool and report back

```python
# YOU run the actual function
result = 2 + 2  # = 4

# YOU tell the LLM what happened by appending two messages:
messages.append({"role": "assistant", "content": response.content})
messages.append({
    "role": "user",
    "content": [
        {
            "type": "tool_result",
            "tool_use_id": "toolu_abc123",   # matches the tool call ID
            "content": "4",                   # your result as a string
        }
    ],
})
```

Now messages looks like:

```
messages = [
    user:      "What is 2+2?"
    assistant: [tool_use: add_numbers(a=2, b=2)]
    user:      [tool_result: "4"]
]
```

### Step 4: Call the LLM again

```python
response = client.messages.create(
    model="claude-opus-4-8",
    messages=messages,   # full history including tool call + result
    tools=tools,
)
```

Now the LLM sees: "I asked to add 2+2, the result was 4." It can
either:
- Return text: `"2 + 2 = 4"` (stop_reason = "end_turn")
- Call another tool (stop_reason = "tool_use")

### Step 5: The loop

That's it. The whole pattern is a while loop:

```python
messages = [{"role": "user", "content": user_query}]

while True:
    response = client.messages.create(
        model="claude-opus-4-8",
        messages=messages,
        tools=tools,
    )

    if response.stop_reason == "end_turn":
        # LLM is done talking. Print final answer.
        print(response.content[0].text)
        break

    # LLM wants to use a tool
    tool_call = response.content[0]  # the ToolUseBlock

    # Execute it (you write this part)
    result = execute_tool(tool_call.name, tool_call.input)

    # Append both the LLM's request and your result
    messages.append({"role": "assistant", "content": response.content})
    messages.append({"role": "user", "content": [
        {
            "type": "tool_result",
            "tool_use_id": tool_call.id,
            "content": str(result),
        }
    ]})

    # Loop again -- LLM sees the result and decides next action
```

### Step 6: execute_tool is just an if/elif

```python
def execute_tool(name: str, args: dict) -> str:
    if name == "add_numbers":
        return str(args["a"] + args["b"])

    elif name == "run_pms1":
        plan, stencil = run_pms1_pipeline(args["query"])
        return json.dumps(serialize_stencil(plan, stencil))

    elif name == "askUserQuestion":
        answer = input(f"[HARNESS] {args['question']}: ")
        return answer

    else:
        return f"Unknown tool: {name}"
```

**That's the whole thing.** `askUserQuestion` is just another tool. The
LLM calls it, your `execute_tool` does `input()`, the user's answer
goes back as a `tool_result`, the LLM sees it on the next loop
iteration.

---

## v1: Naive (superseded by v2 below — kept to show evolution)

In v1, PTECA returns `needs_clarification` to the orchestrator, the
orchestrator relays to user via `askUserQuestion`, and re-calls PTECA
with `user_guidance`. Full stencil JSON lives in orchestrator context.
Both problems are fixed in v2.

### v1 Tool definitions

```python
tools = [
    {
        "name": "run_pms1",
        "description": "Run PMS1 pipeline (Sekei -> PTO -> stencil). "
                       "Returns filled stencil as JSON.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_pteca",
        "description": "Trim stencil rows and split into chart tables. "
                       "Returns list of chart_input dicts OR a "
                       "clarification request if unsure how to split/trim.",
        "input_schema": {
            "type": "object",
            "properties": {
                "stencil_json": {"type": "string"},
                "query": {"type": "string"},
                "user_guidance": {
                    "type": "string",
                    "description": "Optional. User's answer to a prior "
                                   "PTECA clarification question.",
                },
            },
            "required": ["stencil_json", "query"],
        },
    },
    {
        "name": "run_stencil2chart",
        "description": "Render one chart_input dict as an SVG line chart.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_input_json": {"type": "string"},
            },
            "required": ["chart_input_json"],
        },
    },
    {
        "name": "askUserQuestion",
        "description": "Ask the user a clarifying question.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    },
]
```

### v1 execute_tool dispatch

```python
import json
from pathlib import Path
from src.config import Config
from src.index_store import IndexManager
from src.orchestrator import run as run_orchestrator
from src.sekei import sekei
from src.stencil2chart import stencil2chart

def execute_tool(name: str, args: dict, index, config) -> str:

    if name == "run_pms1":
        plan = sekei(args["query"], config)
        filled = run_orchestrator(plan, index, config)
        result = serialize_stencil(plan, filled)
        return json.dumps(result)

    elif name == "run_pteca":
        from src.pteca import pteca
        output = pteca(
            stencil_json=args["stencil_json"],
            query=args["query"],
            user_guidance=args.get("user_guidance"),
            config=config,
        )
        return json.dumps(output)

    elif name == "run_stencil2chart":
        chart_input = json.loads(args["chart_input_json"])
        path = stencil2chart(chart_input, config.output_dir)
        if path:
            return f"Saved: {path}"
        else:
            return "Cancelled or terminated by user."

    elif name == "askUserQuestion":
        answer = input(f"\n[HARNESS] {args['question']}\n> ")
        return answer

    else:
        return f"Unknown tool: {name}"
```

### v1 harness loop

```python
def run_harness(user_query: str, index, config):
    """Top-level agent loop. LLM decides tool call order."""
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_query}]

    system_prompt = (
        "You are a financial data pipeline orchestrator. "
        "Given a user query about financial data:\n"
        "1. Call run_pms1 to get the filled stencil\n"
        "2. Call run_pteca to trim and split for charting\n"
        "3. If PTECA returns needs_clarification, call "
        "askUserQuestion and re-call run_pteca with guidance\n"
        "4. For each chart_input returned, call run_stencil2chart\n"
        "5. Report results to the user"
    )

    while True:
        response = client.messages.create(
            model="claude-opus-4-8",
            system=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    print(block.text)
            break

        # Find tool_use blocks in response
        tool_blocks = [
            b for b in response.content
            if b.type == "tool_use"
        ]

        if not tool_blocks:
            break

        # Execute each tool call
        # (LLM might call multiple tools in one response)
        tool_results = []
        for tb in tool_blocks:
            result = execute_tool(tb.name, tb.input, index, config)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": result,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return messages  # full conversation for audit
```

---

## v1 unrolled example (superseded)

```
ITERATION 1:
  messages = [user: "Chart Best Buy's margins FY2022-2023"]
  LLM thinks: "I need the stencil first"
  LLM returns: tool_use -> run_pms1(query="Best Buy margins FY2022-2023")

ITERATION 2:
  messages = [..., tool_result: "{A1: Revenue 51761, A2: GP 11640, ...}"]
  LLM thinks: "Got stencil. Now trim it for charting."
  LLM returns: tool_use -> run_pteca(stencil_json="...", query="...")

ITERATION 3:
  messages = [..., tool_result: "{needs_clarification: 'Include Revenue
              as a separate chart or only show margins?'}"]
  LLM thinks: "PTECA is unsure. I should ask the user."
  LLM returns: tool_use -> askUserQuestion(question="PTECA wants to know:
              should Revenue be a separate chart, or only show margins?")

ITERATION 4:
  messages = [..., tool_result: "only margins"]
  LLM thinks: "User said only margins. Re-call PTECA with guidance."
  LLM returns: tool_use -> run_pteca(stencil_json="...", query="...",
              user_guidance="only margins, drop Revenue and GP rows")

ITERATION 5:
  messages = [..., tool_result: "[{title: 'Best Buy Margins', ...}]"]
  LLM thinks: "Got 1 chart_input. Render it."
  LLM returns: tool_use -> run_stencil2chart(chart_input_json="...")

ITERATION 6:
  messages = [..., tool_result: "Saved: output/stencil2charted_20260714_1.svg"]
  LLM thinks: "Done."
  LLM returns: end_turn -> "Chart saved to output/stencil2charted_20260714_1.svg"
```

---

## v1 architecture diagram (superseded)

```
USER QUERY (natural language, via CLI)
    |
    v
+-----------------------------------------------+
|  HARNESS (harness.py)                         |
|  Single agentic while-loop                    |
|  LLM = claude-opus-4-8                        |
|  tools = [run_pms1, run_pteca,                |
|           run_stencil2chart, askUserQuestion]  |
|                                               |
|  The LLM IS the orchestrator.                 |
|  It decides which tool to call and when.      |
|  It relays PTECA clarification questions      |
|  to the user via askUserQuestion.             |
+-------+-------+--------+----------+----------+
        |       |        |          |
        v       v        v          v
   run_pms1  run_pteca  run_s2c  askUserQuestion
     |          |         |          |
     v          v         v          v
  sekei()    pteca()   stencil    input()
  orch.run()  (1 LLM    2chart()
  compute_    call,
  stencil()   returns
              charts or
              clarify)
```

---

## v1 what to build (superseded)

```
harness.py              <- the while loop + execute_tool + tool defs
                           + serialize_stencil helper
                           (~100 lines, lives at project root)

src/pteca.py            <- a FUNCTION (not an agent loop)
                           takes stencil_json + query + optional guidance
                           makes ONE LLM call (judge opus profile)
                           returns {charts: [...]} or
                                   {needs_clarification: "..."}
                           (~60 lines)

src/stencil2chart.py    <- already built

src/orchestrator.py     <- already built (this IS run_pms1 under the hood)
```

### v1: PTECA is a dumb function, not an agent (superseded)

PTECA does NOT have its own tool loop. It makes one LLM call, the LLM
returns JSON, PTECA parses and returns it. If the LLM is unsure, it
returns `{needs_clarification: "..."}` and the **harness** LLM (not
PTECA) decides to ask the user via `askUserQuestion` and then re-calls
PTECA.

User interaction lives at the top level only. No nested agent loops.
No routing problem. No breadcrumbs needed for MVP.

---

## v1 limitations (superseded — see v2 limitations below)

---

---

## v2: Opaque handles + sub-tool user interaction

Everything below is the current design. v1 above is kept for
context on how we got here.

---

## Opaque variable handles (context bloat prevention)

### The problem

In a naive tool loop, `tool_result.content` contains the full output
string. When run_pms1 returns a filled stencil, that's ~2K tokens of
JSON dumped into the orchestrator's message history. The LLM re-reads
it every iteration. 6 iterations = ~12K wasted input tokens. The
orchestrator doesn't need to read stencil data to decide "now call
PTECA."

### The fix

Tools return **opaque variable handles** instead of full data. The
actual data lives in a Python-side registry dict. The orchestrator LLM
only sees the handle. When it passes the handle as an argument to the
next tool, `execute_tool` resolves the handle from the registry and
passes the real data to the function.

### Not a breaking change

`tool_result.content` is just a string — the API doesn't care what's
in it. `"$stencil_1"` is as valid as a 2000-token JSON blob. Tool
definitions already have `description` fields — you describe the
return as "returns a variable handle, pass it to downstream tools."
The LLM learns to route handles instead of reading data.

### Implementation

```python
# Python-side registry (lives in harness.py, not visible to LLM)
_registry: dict[str, any] = {}
_counter = 0

def _store(value) -> str:
    """Store a value, return an opaque handle."""
    global _counter
    _counter += 1
    handle = f"$var_{_counter}"
    _registry[handle] = value
    return handle

def _resolve(arg: str):
    """If arg is a handle, resolve it. Otherwise return as-is."""
    if arg in _registry:
        return _registry[arg]
    return arg
```

### Updated execute_tool

```python
def execute_tool(name: str, args: dict, index, config) -> str:

    if name == "run_pms1":
        plan = sekei(args["query"], config)
        filled = run_orchestrator(plan, index, config)
        result = serialize_stencil(plan, filled)
        handle = _store(result)
        return handle           # LLM sees "$var_1", not 2K tokens

    elif name == "run_pteca":
        stencil_data = _resolve(args["stencil_json"])  # resolve handle
        from src.pteca import pteca
        # pteca() is an internal agent loop. It may print()/input()
        # to the CLI to ask the user questions. The orchestrator
        # LLM never sees those interactions — just waits for the
        # handle to come back.
        charts = pteca(
            stencil_data=stencil_data,
            query=args["query"],
            config=config,
        )
        handle = _store(charts)
        return handle             # always a handle, never clarification

    elif name == "run_stencil2chart":
        chart_data = _resolve(args["chart_input_json"])
        if isinstance(chart_data, str):
            chart_data = json.loads(chart_data)
        path = stencil2chart(chart_data, config.output_dir)
        if path:
            return f"Saved: {path}"  # small string, OK in context
        else:
            return "Cancelled or terminated by user."

    elif name == "askUserQuestion":
        answer = input(f"\n[HARNESS] {args['question']}\n> ")
        return answer

    else:
        return f"Unknown tool: {name}"
```

### Updated tool descriptions

Tool descriptions tell the LLM that return values are opaque handles.
Tools handle their own user interaction internally via CLI — the
orchestrator LLM does not relay questions.

```python
tools = [
    {
        "name": "run_pms1",
        "description": "Run PMS1 pipeline (Sekei -> PTO -> stencil). "
                       "Returns a variable handle (e.g. $var_1) representing "
                       "the filled stencil. Pass this handle to run_pteca's "
                       "stencil_json parameter. Do NOT try to read or parse "
                       "the handle — it is opaque. This tool may ask the "
                       "user questions via CLI if it encounters problems "
                       "(e.g. ambiguous entity, missing data). Those "
                       "interactions are internal — you will not see them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_pteca",
        "description": "Trim stencil rows and split into chart tables. "
                       "stencil_json accepts a variable handle from run_pms1. "
                       "Returns a variable handle containing chart_input "
                       "dicts — pass to run_stencil2chart. This tool may ask "
                       "the user questions via CLI to clarify trimming/"
                       "splitting preferences. Those interactions are "
                       "internal — you will not see them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "stencil_json": {"type": "string"},
                "query": {"type": "string"},
            },
            "required": ["stencil_json", "query"],
        },
    },
    {
        "name": "run_stencil2chart",
        "description": "Render a chart as SVG. chart_input_json accepts a "
                       "variable handle from run_pteca. This tool may prompt "
                       "the user via CLI if there are missing values.",
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_input_json": {"type": "string"},
            },
            "required": ["chart_input_json"],
        },
    },
    {
        "name": "askUserQuestion",
        "description": "Ask the user a clarifying question. Use this ONLY "
                       "for orchestrator-level questions (e.g. 'Which firms "
                       "should I run?', 'Do you also want charts?'). Do NOT "
                       "use this to relay questions from sub-tools — they "
                       "handle their own user interaction internally.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
    },
]
```

### v2 harness loop

Same while-loop structure as v1, but updated system prompt — no more
PTECA relay instructions.

```python
def run_harness(user_query: str, index, config):
    """Top-level agent loop. LLM decides tool call order."""
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": user_query}]

    system_prompt = (
        "You are a financial data pipeline orchestrator. "
        "Given a user query about financial data:\n"
        "1. Call run_pms1 to get a stencil handle per firm\n"
        "2. Call run_pteca per stencil to get chart_input handles\n"
        "3. Call run_stencil2chart per chart_input to render SVGs\n"
        "4. Report results to the user\n\n"
        "Tools return opaque variable handles ($var_N). Pass them "
        "between tools — do not try to read or parse them.\n\n"
        "Tools handle their own user interaction via CLI. If a tool "
        "needs to ask the user something, it does so internally — "
        "you will not see the exchange.\n\n"
        "Use askUserQuestion ONLY for your own orchestrator-level "
        "questions (e.g. 'Which firms should I run?')."
    )

    while True:
        response = client.messages.create(
            model="claude-opus-4-8",
            system=system_prompt,
            messages=messages,
            tools=tools,
            max_tokens=4096,
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    print(f"[ORCHESTRATOR] {block.text}")
            break

        tool_blocks = [
            b for b in response.content
            if b.type == "tool_use"
        ]

        if not tool_blocks:
            break

        tool_results = []
        for tb in tool_blocks:
            result = execute_tool(tb.name, tb.input, index, config)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tb.id,
                "content": result,
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

    return messages
```

### What the orchestrator LLM sees (unrolled)

```
ITERATION 1:
  LLM calls: run_pms1(query="Best Buy margins FY2022-2023")
  tool_result: "$var_1"                    <- 5 tokens, not 2000

ITERATION 2:
  LLM calls: run_pms1(query="Amcor margins FY2022-2023")
  tool_result: "$var_2"                    <- 5 tokens

ITERATION 3:
  LLM calls: run_pteca(stencil_json="$var_1", query="...")
  execute_tool resolves $var_1 -> full stencil, passes to pteca()
  tool_result: "$var_3"                    <- chart_inputs for BBY

ITERATION 4:
  LLM calls: run_pteca(stencil_json="$var_2", query="...")
  tool_result: "$var_4"                    <- chart_inputs for Amcor

ITERATION 5:
  LLM calls: run_stencil2chart(chart_input_json="$var_3")
  tool_result: "Saved: output/stencil2charted_20260714_1.svg"

ITERATION 6:
  LLM calls: run_stencil2chart(chart_input_json="$var_4")
  tool_result: "Saved: output/stencil2charted_20260714_2.svg"

ITERATION 7:
  LLM returns end_turn: "Done. 2 charts saved."
```

Total context the orchestrator LLM carries: ~200 tokens of handles
and file paths, not ~8K of stencil JSON.

### Sub-tool user interaction (does not leak to orchestrator)

Each tool can do its own `print()` / `input()` internally. If PMS1
hits a problem (judge returns insufficient, entity filter empty), it
can prompt the user directly inside `execute_tool("run_pms1", ...)`.
The orchestrator LLM never sees that interaction — it just waits for
the tool call to return a handle. The orchestrator already knows what
it called PMS1 for (it chose the query), so it doesn't need to see
the internal resolution.

```
Orchestrator LLM context:          User's CLI:
  tool_use: run_pms1("BBY...")       [PMS1] Entity filter returned 0
  (waiting...)                       chunks for "Best Buy" in FY2021.
                                     Relax FY filter? [y/n]: y
                                     [PMS1] Relaxed. Found 42 chunks.
                                     [PMS1] Continuing...
  tool_result: "$var_1"             
```

The orchestrator sees only "$var_1". The user saw and resolved the
PMS1 problem. Neither leaked into the other's context.

---

## v2 what to build (file list)

```
harness.py              <- the while loop + execute_tool + tool defs
                           + _store/_resolve registry
                           (~120 lines, lives at project root)

src/pteca.py            <- INTERNAL AGENT LOOP (not a dumb function)
                           takes stencil_data (resolved dict) + query
                           runs its own agentic LLM loop:
                             - LLM decides which rows to drop,
                               how to split by unit
                             - if unsure: print("[PTECA] ...") +
                               input() to ask user directly
                             - loops until LLM is confident
                           returns list[dict] (chart_input dicts)
                           uses judge opus profile
                           (~80-100 lines)

src/stencil2chart.py    <- already built

src/orchestrator.py     <- already built (this IS run_pms1 under the hood)
```

### PTECA is an internal agent loop

PTECA has its own tool-use loop inside `execute_tool("run_pteca", ...)`.
It runs the same pattern as the top-level harness (messages list,
while loop, tool calls), but scoped to its own task (trim + split
stencil). Its only tool is `askUserQuestion` (prints `[PTECA] ...`
to CLI, reads `input()`).

The orchestrator LLM never sees PTECA's internal conversation. It
just waits for `execute_tool` to return a handle.

```python
# Inside src/pteca.py (sketch)

def pteca(stencil_data: dict, query: str, config) -> list[dict]:
    """Internal agent loop: trim stencil, split by unit, return chart_inputs."""
    import anthropic

    client = anthropic.Anthropic()

    pteca_tools = [
        {
            "name": "askUserQuestion",
            "description": "Ask the analyst a question about how to "
                           "trim or split the stencil for charting.",
            "input_schema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
            },
        },
    ]

    messages = [{
        "role": "user",
        "content": (
            f"Original query: {query}\n\n"
            f"Filled stencil:\n{json.dumps(stencil_data, indent=2)}\n\n"
            "Trim unnecessary helper rows. Split into separate chart_input "
            "dicts where units are not directly comparable. Generate title "
            "and denomination_label for each chart. If unsure about "
            "trimming or splitting, ask the user."
        ),
    }]

    while True:
        response = client.messages.create(
            model="claude-opus-4-8",  # judge opus profile
            messages=messages,
            tools=pteca_tools,
            max_tokens=4096,
        )

        if response.stop_reason == "end_turn":
            # Parse chart_inputs from final response
            return json.loads(response.content[0].text)

        # Tool call — must be askUserQuestion
        tb = response.content[0]
        print(f"\n[PTECA] {tb.input['question']}")
        answer = input("> ")

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tb.id, "content": answer}
        ]})
```

PMS1 can follow the same pattern if it needs user interaction
internally (e.g. entity filter empty, FY ambiguity). Each tool
owns its own `print("[TOOLNAME] ...")` + `input()`.

---

## CLI multiplexing

The CLI (terminal) is shared by all tools. No routing framework
needed. Tools identify themselves with labels:

```
[ORCHESTRATOR] Which firms should I run?
> Best Buy and Amcor

[ORCHESTRATOR] Running PMS1 for Best Buy...
[PMS1] Loading index...
[PMS1] Sekei planning... done.
[PMS1] PTO batch A_124... 42 chunks filtered.
[PMS1] Judge... tokens in=3200 out=450
[PMS1] Done.

[ORCHESTRATOR] Running PMS1 for Amcor...
[PMS1] Loading index...
[PMS1] Entity filter returned 0 chunks for FY2021.
[PMS1] Relax fiscal year filter? [y/n]
> y
[PMS1] Relaxed. Found 38 chunks.
[PMS1] Done.

[ORCHESTRATOR] Running PTECA for Best Buy stencil...
[PTECA] Stencil has 5 rows: Revenue, GP, Gross Margin, NI, Net Margin.
[PTECA] Include Revenue and Gross Profit as separate USD chart,
        or only show margin percentages?
> only margins
[PTECA] 1 chart (% only). Generating...
[PTECA] Done.

[ORCHESTRATOR] Running PTECA for Amcor stencil...
[PTECA] Same structure as Best Buy. Only margins?
> yes
[PTECA] Done.

[ORCHESTRATOR] Rendering charts...
[ORCHESTRATOR] Saved: output/stencil2charted_20260714_1.svg
[ORCHESTRATOR] Saved: output/stencil2charted_20260714_2.svg
[ORCHESTRATOR] Done. 2 charts saved.
```

**Why this works without routing:** tools execute sequentially inside
`execute_tool`. Only one tool runs at a time. stdin/stdout are never
contested. Each tool labels its output with `[TOOLNAME]`. First come
first serve per turn.

**When this breaks:** if you want parallel tool execution (run PMS1
for firm A and firm B simultaneously in threads). Then two tools
could both `input()` at the same time — stdin collision. That's the
PUS problem (message bus / event queue). Not MVP.

---

## v2 limitations (known, accepted for MVP)

- **Sequential only.** The harness calls tools one at a time. No
  parallel PTO batches through the harness (PMS1 internally
  parallelizes, but the harness sees PMS1 as one atomic tool call).

- **No parallel user questions.** Only one tool can `input()` at a
  time. This is the fundamental gap vs the PUS vision (multiple
  agents paused simultaneously). Accepted for MVP.

- **stencil2chart gap prompt is separate from PTECA.** stencil2chart
  has its own `input()` for missing-value prompts. Could unify later
  by having PTECA pre-validate values before passing to stencil2chart.

- **Orchestrator LLM can't inspect stencil contents.** It sees only
  handles. If the orchestrator needs to make content-dependent
  decisions (e.g. "this stencil failed, retry with different query"),
  it has no data to reason about. Sub-tools must surface failures as
  small status strings, not handles.

---

## Path to PUS (future, not MVP)

The PUS endgame has:
- Any-depth user interaction with breadcrumb context
- Multiple agents paused simultaneously
- Agent identity shown to user

To get there from Pattern A:
1. Replace `execute_tool`'s `input()` with a message bus / event queue
2. Give each tool call a breadcrumb (caller chain + context)
3. Run tool calls in threads, each can post to the queue
4. Main thread reads queue, multiplexes questions to CLI
5. Each tool thread blocks on its own answer channel

This is a fundamentally different architecture. Pattern A (this doc)
does not scale to it — it would be a rewrite, not a refactor. That's
fine. MVP validates the pipeline logic. PUS validates the interaction
model. Different bets.
