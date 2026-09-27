# magicpin AI Challenge Submission - Vera

## Approach
This submission implements a robust, deterministic message composer for the magicpin AI Challenge. 
The core design involves a single-prompt composer powered by the `gemini-3.8-flash` model, using a structured JSON output schema to ensure all constraints are strictly met. 
The system runs via a FastAPI application implementing the required `/v1/healthz`, `/v1/metadata`, `/v1/context`, `/v1/tick`, and `/v1/reply` endpoints.

The prompt injects the 4 contexts (Category, Merchant, Customer, and Trigger) into the LLM and enforces strict rules around:
1. **Specificity & Grounding**: Forcing the model to anchor on verifiable facts from the provided context (e.g., specific missed search counts, or exact offer prices).
2. **Category & Merchant Fit**: Adapting the voice, tone, and language (e.g., Hindi-English code-mix) based on the merchant's identity and category requirements.
3. **Engagement Compulsion**: Generating a clear, single binary CTA (e.g., YES/STOP) to minimize friction and maximize response rates.
4. **Intent Recognition**: A multi-turn handler in `/v1/reply` that detects auto-replies, explicit intents, and objections, determining whether to send a follow-up, wait, or end gracefully.

## Tradeoffs Made
1. **State Management**: Context and conversations are stored entirely in memory. While this keeps the solution simple and lightweight for the 60-minute simulated testing window, a production deployment would require migrating this state to Redis or a relational database for persistence across service restarts and distributed instances.
2. **LLM Dependency on Every Tick**: The bot relies entirely on real-time LLM inference per trigger. While Gemini 3.8 Flash is extremely fast, a more highly optimized version could use static templating (with LLM pre-approval) for common repetitive triggers (e.g., standard recall reminders) to reduce API latency and cost.
3. **Context Injection Depth**: The entire merchant and category contexts are injected raw into the prompt. As the datasets scale, this might exceed context windows or lead to diluted attention. A retrieval-augmented approach (RAG) over the category digest could yield better cost-efficiency and precision.

## Additional Context That Would Have Helped
- **Historical Conversion Rates**: Knowing which types of triggers historically convert best for specific merchant types would allow prioritizing higher-ROI triggers when rate limits (20 actions/tick) become a bottleneck.
- **Detailed Language Preferences**: More granular context on the merchant's previous conversation style (e.g., whether they respond better to short bullet points vs conversational paragraphs) would help dial in the personalization even further.
- **Competitor Landscape Data**: Insight into the specific competitors around a merchant would enable even more compelling loss-aversion messaging (e.g., "3 salons within 2km of you just launched this campaign").
