import { ResourcePage, type ResourceSpec } from "./ResourcePage";

const PROVIDERS: ResourceSpec = {
  sectionKey: "providers",
  title: "Providers",
  subtitle: "LLM backends that models run on.",
  noun: "provider",
  typeField: "type",
  typeOptions: ["ollama", "openrouter", "openai_compatible"],
  test: "provider",
  fields: [
    {
      name: "base_url",
      label: "Base URL",
      onlyForType: "ollama",
      hint: "e.g. http://host.docker.internal:11434",
    },
    {
      name: "api_key",
      label: "API key",
      onlyForType: "openrouter",
      secret: true,
      hint: "Reference a secret with $OPENROUTER_API_KEY, or paste a literal key.",
    },
    {
      name: "base_url",
      label: "Base URL",
      onlyForType: "openai_compatible",
      hint: "OpenAI-compatible endpoint, e.g. http://host.docker.internal:8000/v1 or https://api.openai.com/v1",
    },
    {
      name: "api_key",
      label: "API key",
      onlyForType: "openai_compatible",
      secret: true,
      hint: "Optional. Reference a secret with $OPENAI_API_KEY, paste a literal key, or leave blank for key-less local servers.",
    },
  ],
};

const MODELS: ResourceSpec = {
  sectionKey: "models",
  title: "Models",
  subtitle: "Named models, each bound to a provider.",
  noun: "model",
  fields: [
    { name: "provider", label: "Provider", optionsFrom: "providers" },
    {
      name: "model",
      label: "Model name",
      hint: "e.g. llama3, anthropic/claude-3.5-sonnet. Pick a provider, then Discover to list available models.",
      discoverFrom: "provider",
    },
    {
      name: "vision",
      label: "Vision",
      boolean: true,
      hint: "Enable if this model can see images. Lets it receive pictures the user sends in chat.",
    },
  ],
};

const CHANNELS: ResourceSpec = {
  sectionKey: "channels",
  title: "Channels",
  subtitle: "How users talk to the agent.",
  noun: "channel",
  typeField: "type",
  typeOptions: ["matrix", "cli"],
  test: "channel",
  fields: [
    { name: "homeserver", label: "Homeserver", onlyForType: "matrix", hint: "e.g. https://matrix.org" },
    { name: "user_id", label: "User ID", onlyForType: "matrix", hint: "e.g. @memtrix:matrix.org" },
    { name: "access_token", label: "Access token", onlyForType: "matrix", secret: true },
  ],
};

const AGENTS: ResourceSpec = {
  sectionKey: "agents",
  title: "Sub-Agents",
  subtitle: "Additional agents, each with its own model and channel.",
  noun: "sub-agent",
  fields: [
    { name: "model", label: "Model", optionsFrom: "models" },
    { name: "channel", label: "Channel", optionsFrom: "channels" },
  ],
};

export const ProvidersPage = () => <ResourcePage spec={PROVIDERS} />;
export const ModelsPage = () => <ResourcePage spec={MODELS} />;
export const ChannelsPage = () => <ResourcePage spec={CHANNELS} />;
export const AgentsPage = () => <ResourcePage spec={AGENTS} />;
