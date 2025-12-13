"""Ollama LLM integration for email classification."""

import json
from pathlib import Path

import httpx
from dataclasses import dataclass

from .config import OllamaConfig
from .content import extract_email_summary

# Directory containing prompt templates
PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {prompt_path}")
    return prompt_path.read_text()


@dataclass
class ClassificationResult:
    predicted_folder: str
    secondary_labels: list[str]
    confidence: float


@dataclass
class FolderDescription:
    folder_id: str
    description: str


@dataclass
class SuggestedFolder:
    name: str
    description: str
    example_criteria: list[str]


class OllamaClient:
    def __init__(self, config: OllamaConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "OllamaClient":
        self._client = httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=httpx.Timeout(self.config.timeout_seconds),
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Client not initialized. Use async context manager.")
        return self._client

    async def _generate(self, prompt: str) -> str:
        """Send a generation request to Ollama."""
        response = await self.client.post(
            "/api/generate",
            json={
                "model": self.config.model,
                "prompt": prompt,
                "stream": False,
            },
        )
        response.raise_for_status()
        return response.json()["response"]

    async def classify_email(
        self,
        subject: str,
        from_addr: str,
        body: str,
        folder_descriptions: dict[str, str],
        confidence_threshold: float = 0.5,
        fallback_folder: str | None = None,
    ) -> ClassificationResult:
        """Classify an email into one of the available folders."""
        import logging
        logger = logging.getLogger("mailmap")

        folders_text = "\n".join(
            f"- {folder_id}: {desc}" for folder_id, desc in folder_descriptions.items()
        )
        valid_folders = set(folder_descriptions.keys())

        # Find fallback folder - look for miscellaneous/uncategorized, or use first folder
        if fallback_folder is None:
            fallback_candidates = ["MiscellaneousAndUncategorized", "Miscellaneous", "Uncategorized", "INBOX"]
            for candidate in fallback_candidates:
                if candidate in valid_folders:
                    fallback_folder = candidate
                    break
            if fallback_folder is None and valid_folders:
                fallback_folder = next(iter(valid_folders))

        # Clean email content before sending to LLM
        cleaned = extract_email_summary(subject, from_addr, body, max_body_length=500)

        prompt_template = load_prompt("classify_email")
        prompt = prompt_template.format(
            folders_text=folders_text,
            from_addr=cleaned["from_addr"],
            subject=cleaned["subject"],
            body=cleaned["body"],
        )

        response_text = await self._generate(prompt)

        predicted_folder = fallback_folder
        secondary_labels = []
        confidence = 0.0

        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response_text[start:end])
                predicted_folder = data.get("predicted_folder", fallback_folder)
                secondary_labels = data.get("secondary_labels", [])
                confidence = float(data.get("confidence", 0.0))
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse classification response: {e}")

        # Validate: folder must exist in our list
        if predicted_folder not in valid_folders:
            logger.warning(f"LLM returned invalid folder '{predicted_folder}', using fallback")
            predicted_folder = fallback_folder
            confidence = 0.0

        # Filter: low confidence goes to fallback
        if confidence < confidence_threshold:
            logger.info(f"Low confidence ({confidence:.2f}), routing to {fallback_folder}")
            predicted_folder = fallback_folder

        return ClassificationResult(
            predicted_folder=predicted_folder,
            secondary_labels=secondary_labels,
            confidence=confidence,
        )

    async def generate_folder_description(
        self, folder_name: str, sample_emails: list[dict[str, str]]
    ) -> FolderDescription:
        """Generate a description for a folder based on sample emails."""
        samples_text = ""
        for i, email in enumerate(sample_emails[:5], 1):
            # Clean each sample email
            cleaned = extract_email_summary(
                email.get('subject', 'no subject'),
                email.get('from_addr', 'unknown'),
                email.get('body', ''),
                max_body_length=200,
            )
            samples_text += f"""
Email {i}:
  From: {cleaned['from_addr']}
  Subject: {cleaned['subject']}
  Preview: {cleaned['body']}
"""

        prompt_template = load_prompt("generate_folder_description")
        prompt = prompt_template.format(
            folder_name=folder_name,
            samples_text=samples_text,
        )

        response_text = await self._generate(prompt)
        description = response_text.strip()

        return FolderDescription(folder_id=folder_name, description=description)

    async def suggest_folder_structure(
        self, sample_emails: list[dict[str, str]], max_emails: int = 250
    ) -> list[SuggestedFolder]:
        """Analyze sample emails and suggest a folder structure for organizing them."""
        samples_text = ""
        for i, email in enumerate(sample_emails[:max_emails], 1):
            # Clean each sample email
            cleaned = extract_email_summary(
                email.get('subject', 'no subject'),
                email.get('from_addr', 'unknown'),
                email.get('body', ''),
                max_body_length=150,
            )
            samples_text += f"""
Email {i}:
  From: {cleaned['from_addr']}
  Subject: {cleaned['subject']}
  Preview: {cleaned['body']}
"""

        prompt_template = load_prompt("suggest_folder_structure")
        actual_count = min(len(sample_emails), max_emails)
        prompt = prompt_template.format(
            samples_text=samples_text,
            email_count=actual_count,
        )

        import logging
        logging.getLogger("mailmap").info(f"Prompt size: {len(prompt)} chars, {actual_count} emails included")

        response_text = await self._generate(prompt)

        try:
            start = response_text.find("[")
            end = response_text.rfind("]") + 1
            if start >= 0 and end > start:
                data = json.loads(response_text[start:end])
                folders = []
                for item in data:
                    folders.append(SuggestedFolder(
                        name=item.get("name", "Unknown"),
                        description=item.get("description", ""),
                        example_criteria=item.get("example_criteria", []),
                    ))
                return folders
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

        # Fallback: return just INBOX
        return [SuggestedFolder(
            name="INBOX",
            description="General incoming mail that doesn't fit other categories",
            example_criteria=["Uncategorized emails", "New contacts"],
        )]

    async def refine_folder_structure(
        self,
        sample_emails: list[dict[str, str]],
        existing_categories: list[SuggestedFolder],
        batch_num: int,
        batch_size: int = 100,
    ) -> tuple[list[SuggestedFolder], list[dict]]:
        """
        Iteratively refine folder structure with a new batch of emails.

        Returns (updated_categories, email_assignments).
        """
        # Format existing categories with descriptions for semantic matching
        if existing_categories:
            categories_text = "\n".join(
                f"- {cat.name}: {cat.description}" for cat in existing_categories
            )
        else:
            categories_text = "(none yet - first batch)"

        # Format email samples
        samples_text = ""
        for i, email in enumerate(sample_emails[:batch_size], 1):
            cleaned = extract_email_summary(
                email.get('subject', 'no subject'),
                email.get('from_addr', 'unknown'),
                email.get('body', ''),
                max_body_length=150,
            )
            samples_text += f"""
Email {i}:
  From: {cleaned['from_addr']}
  Subject: {cleaned['subject']}
  Preview: {cleaned['body']}
"""

        prompt_template = load_prompt("refine_folder_structure")
        prompt = prompt_template.format(
            existing_categories=categories_text,
            samples_text=samples_text,
            batch_num=batch_num,
        )

        import logging
        logging.getLogger("mailmap").info(
            f"Refine batch {batch_num}: {len(sample_emails)} emails, "
            f"{len(existing_categories)} existing categories"
        )

        response_text = await self._generate(prompt)

        # Try to parse JSON, with repair fallback
        json_str = None
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            json_str = response_text[start:end]

        data = None
        if json_str:
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                # Try to repair the JSON
                import logging
                logging.getLogger("mailmap").info("Attempting JSON repair...")
                repaired = await self.repair_json(json_str)
                if repaired:
                    try:
                        data = json.loads(repaired)
                        logging.getLogger("mailmap").info("JSON repair successful")
                    except json.JSONDecodeError:
                        pass

        try:
            if data:
                # Parse email assignments
                assignments = data.get("email_assignments", [])

                # Build categories from BOTH explicit list AND assignments
                # This ensures we capture all categories the LLM actually used
                category_map = {}

                # First, add explicitly defined categories with descriptions
                for item in data.get("categories", []):
                    name = item.get("name", "Unknown")
                    category_map[name] = SuggestedFolder(
                        name=name,
                        description=item.get("description", ""),
                        example_criteria=item.get("example_criteria", []),
                    )

                # Then, add any categories from assignments that weren't in the list
                for assignment in assignments:
                    cat_name = assignment.get("category", "Uncategorized")
                    if cat_name not in category_map:
                        category_map[cat_name] = SuggestedFolder(
                            name=cat_name,
                            description=f"Emails assigned to {cat_name}",
                            example_criteria=[],
                        )

                # Preserve existing categories that weren't mentioned
                for existing in existing_categories:
                    if existing.name not in category_map:
                        category_map[existing.name] = existing

                return list(category_map.values()), assignments
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            import logging
            logging.getLogger("mailmap").warning(f"Failed to parse refinement response: {e}")

        # Fallback: return existing categories unchanged
        return existing_categories, []

    async def repair_json(self, broken_json: str) -> str | None:
        """Attempt to repair malformed JSON by asking the LLM to fix it."""
        prompt_template = load_prompt("repair_json")
        prompt = prompt_template.format(broken_json=broken_json[:2000])  # Limit size

        response_text = await self._generate(prompt)

        # Try to extract JSON from response
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = response_text.find(start_char)
            end = response_text.rfind(end_char) + 1
            if start >= 0 and end > start:
                try:
                    json.loads(response_text[start:end])
                    return response_text[start:end]
                except json.JSONDecodeError:
                    continue
        return None

    async def normalize_categories(
        self,
        categories: list[SuggestedFolder],
    ) -> tuple[list[SuggestedFolder], dict[str, str]]:
        """
        Consolidate duplicate/overlapping categories.

        Returns (consolidated_categories, rename_map).
        """
        import logging
        logger = logging.getLogger("mailmap")

        if len(categories) < 2:
            return categories, {c.name: c.name for c in categories}

        # Build lookup for original descriptions
        original_descriptions = {cat.name: cat.description for cat in categories}
        original_names = set(original_descriptions.keys())

        categories_list = "\n".join(
            f"- {cat.name}: {cat.description}"
            for cat in categories
        )

        prompt_template = load_prompt("normalize_categories")
        prompt = prompt_template.format(
            categories_list=categories_list,
            category_count=len(categories),
        )

        logger.info(f"Normalizing {len(categories)} categories...")

        response_text = await self._generate(prompt)

        consolidated = []
        rename_map = {}

        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response_text[start:end])

                # Build consolidated categories
                for item in data.get("consolidated_categories", []):
                    consolidated.append(SuggestedFolder(
                        name=item.get("name", "Unknown"),
                        description=item.get("description", ""),
                        example_criteria=item.get("merged_from", []),
                    ))

                # Build rename map
                rename_map = data.get("rename_map", {})
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse normalization response: {e}")
            return categories, {c.name: c.name for c in categories}

        # Check for missing mappings
        missing = original_names - set(rename_map.keys())
        if missing:
            logger.warning(f"Rename map missing {len(missing)} categories: {missing}")

            # Ask LLM to repair the incomplete mapping
            consolidated, rename_map = await self._repair_rename_map(
                categories, consolidated, rename_map
            )

            # Final check - any still missing get mapped to themselves
            still_missing = original_names - set(rename_map.keys())
            if still_missing:
                logger.warning(f"After repair, still missing {len(still_missing)} - mapping to self")
                consolidated_names = {c.name for c in consolidated}
                for name in still_missing:
                    rename_map[name] = name
                    # Add back with original description if not already in consolidated
                    if name not in consolidated_names:
                        consolidated.append(SuggestedFolder(
                            name=name,
                            description=original_descriptions.get(name, f"Emails in {name}"),
                            example_criteria=[],
                        ))

        return consolidated, rename_map

    async def _repair_rename_map(
        self,
        original_categories: list[SuggestedFolder],
        consolidated: list[SuggestedFolder],
        partial_map: dict[str, str],
    ) -> tuple[list[SuggestedFolder], dict[str, str]]:
        """Ask LLM to complete an incomplete rename map."""
        import logging
        logger = logging.getLogger("mailmap")

        original_names = {c.name for c in original_categories}
        original_by_name = {c.name: c for c in original_categories}
        missing = original_names - set(partial_map.keys())

        # Full context: all original categories
        original_text = "\n".join(
            f"- {c.name}: {c.description}" for c in original_categories
        )

        # Full context: consolidated categories with descriptions
        consolidated_text = "\n".join(
            f"- {c.name}: {c.description}" for c in consolidated
        )

        # The missing categories with their descriptions
        missing_text = "\n".join(
            f"- {name}: {original_by_name[name].description}"
            for name in sorted(missing)
        )

        # Show existing mappings so LLM sees the pattern
        existing_mappings_text = "\n".join(
            f"  {old} -> {new}" for old, new in sorted(partial_map.items())
        )

        prompt = f"""You previously consolidated {len(original_categories)} categories into {len(consolidated)} categories, but the rename_map is missing {len(missing)} entries.

ORIGINAL CATEGORIES (before consolidation):
{original_text}

CONSOLIDATED CATEGORIES (after consolidation):
{consolidated_text}

EXISTING MAPPINGS (already done correctly):
{existing_mappings_text}

MISSING FROM RENAME_MAP (need to be mapped):
{missing_text}

For each missing category, determine which consolidated category it should map to based on semantic similarity.
Look at the existing mappings to understand the consolidation pattern.

OUTPUT JSON only:
{{
  "mappings": {{
    "MissingCategory1": "ConsolidatedCategory",
    "MissingCategory2": "ConsolidatedCategory"
  }}
}}

JSON:
"""
        logger.info(f"Asking LLM to repair {len(missing)} missing mappings...")

        response_text = await self._generate(prompt)

        try:
            start = response_text.find("{")
            end = response_text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(response_text[start:end])
                new_mappings = data.get("mappings", {})

                # Merge into existing map
                for old_name, new_name in new_mappings.items():
                    if old_name in missing:
                        partial_map[old_name] = new_name
                        logger.info(f"  Repaired: {old_name} -> {new_name}")

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse repair response: {e}")

        return consolidated, partial_map
