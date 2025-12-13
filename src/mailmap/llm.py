"""Ollama LLM integration for email classification."""

import json
import logging
from functools import lru_cache
from pathlib import Path

import httpx
from dataclasses import dataclass

from .config import OllamaConfig
from .content import extract_email_summary

# Directory containing prompt templates
PROMPTS_DIR = Path(__file__).parent / "prompts"

# Module-level logger
logger = logging.getLogger("mailmap")


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory (cached).

    Args:
        name: Name of the prompt template (without .txt extension)

    Returns:
        The prompt template content

    Raises:
        ValueError: If name contains path traversal characters
        FileNotFoundError: If prompt template doesn't exist
    """
    # Sanitize input - prevent path traversal
    if '/' in name or '\\' in name or '..' in name:
        raise ValueError(f"Invalid prompt name: {name}")

    prompt_path = PROMPTS_DIR / f"{name}.txt"
    prompt_path = prompt_path.resolve()

    # Ensure path is within PROMPTS_DIR
    if not str(prompt_path).startswith(str(PROMPTS_DIR.resolve())):
        raise ValueError(f"Path traversal attempt: {name}")

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


def _format_email_samples(emails: list[dict[str, str]], max_emails: int, max_body_length: int = 150) -> str:
    """Format email samples for prompt inclusion.

    Args:
        emails: List of email dicts with subject, from_addr, body keys
        max_emails: Maximum number of emails to include
        max_body_length: Maximum body preview length

    Returns:
        Formatted string of email samples
    """
    parts = []
    for i, email in enumerate(emails[:max_emails], 1):
        cleaned = extract_email_summary(
            email.get('subject', 'no subject'),
            email.get('from_addr', 'unknown'),
            email.get('body', ''),
            max_body_length=max_body_length,
        )
        parts.append(f"""
Email {i}:
  From: {cleaned['from_addr']}
  Subject: {cleaned['subject']}
  Preview: {cleaned['body']}""")
    return "\n".join(parts)


class OllamaClient:
    """Async client for Ollama LLM API."""

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

    def _extract_json(self, text: str, start_char: str = '{', end_char: str = '}') -> str | None:
        """Extract JSON from response text.

        Args:
            text: Response text that may contain JSON
            start_char: Starting delimiter ('{' for object, '[' for array)
            end_char: Ending delimiter ('}' for object, ']' for array)

        Returns:
            Extracted JSON string or None if not found
        """
        start = text.find(start_char)
        end = text.rfind(end_char) + 1
        if start >= 0 and end > start:
            return text[start:end]
        return None

    def _parse_json(self, text: str, start_char: str = '{', end_char: str = '}') -> dict | list | None:
        """Extract and parse JSON from response text.

        Args:
            text: Response text that may contain JSON
            start_char: Starting delimiter
            end_char: Ending delimiter

        Returns:
            Parsed JSON or None if extraction/parsing fails
        """
        json_str = self._extract_json(text, start_char, end_char)
        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                return None
        return None

    async def _generate(self, prompt: str) -> str:
        """Send a generation request to Ollama.

        Args:
            prompt: The prompt to send

        Returns:
            The generated response text

        Raises:
            httpx.HTTPError: If the request fails
        """
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
        """Classify an email into one of the available folders.

        Args:
            subject: Email subject line
            from_addr: Sender email address
            body: Email body text
            folder_descriptions: Map of folder_id to description
            confidence_threshold: Minimum confidence for classification (0.0-1.0)
            fallback_folder: Folder to use when confidence is low

        Returns:
            ClassificationResult with predicted folder, labels, and confidence
        """
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

        predicted_folder: str = fallback_folder or "INBOX"
        secondary_labels: list[str] = []
        confidence = 0.0

        data = self._parse_json(response_text)
        if isinstance(data, dict):
            predicted_folder = data.get("predicted_folder", fallback_folder) or "INBOX"
            secondary_labels = data.get("secondary_labels", []) or []
            try:
                confidence = float(data.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
        else:
            logger.warning("Failed to parse classification response")

        # Validate: folder must exist in our list
        if predicted_folder not in valid_folders:
            logger.warning(f"LLM returned invalid folder '{predicted_folder}', using fallback")
            predicted_folder = fallback_folder or "INBOX"
            confidence = 0.0

        # Filter: low confidence goes to fallback
        if confidence < confidence_threshold:
            logger.info(f"Low confidence ({confidence:.2f}), routing to {fallback_folder}")
            predicted_folder = fallback_folder or "INBOX"

        return ClassificationResult(
            predicted_folder=predicted_folder,
            secondary_labels=secondary_labels,
            confidence=confidence,
        )

    async def generate_folder_description(
        self, folder_name: str, sample_emails: list[dict[str, str]]
    ) -> FolderDescription:
        """Generate a description for a folder based on sample emails.

        Args:
            folder_name: Name of the folder
            sample_emails: List of sample emails from the folder

        Returns:
            FolderDescription with generated description
        """
        samples_text = _format_email_samples(sample_emails, max_emails=5, max_body_length=200)

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
        """Analyze sample emails and suggest a folder structure for organizing them.

        Args:
            sample_emails: List of sample emails to analyze
            max_emails: Maximum emails to include in analysis

        Returns:
            List of suggested folders with descriptions
        """
        samples_text = _format_email_samples(sample_emails, max_emails=max_emails, max_body_length=150)

        prompt_template = load_prompt("suggest_folder_structure")
        actual_count = min(len(sample_emails), max_emails)
        prompt = prompt_template.format(
            samples_text=samples_text,
            email_count=actual_count,
        )

        logger.info(f"Prompt size: {len(prompt)} chars, {actual_count} emails included")

        response_text = await self._generate(prompt)

        data = self._parse_json(response_text, '[', ']')
        if data and isinstance(data, list):
            folders = []
            for item in data:
                folders.append(SuggestedFolder(
                    name=item.get("name", "Unknown"),
                    description=item.get("description", ""),
                    example_criteria=item.get("example_criteria", []),
                ))
            return folders

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
        """Iteratively refine folder structure with a new batch of emails.

        Args:
            sample_emails: Batch of emails to process
            existing_categories: Categories from previous batches
            batch_num: Current batch number
            batch_size: Maximum emails per batch

        Returns:
            Tuple of (updated_categories, email_assignments)
        """
        # Format existing categories with descriptions for semantic matching
        if existing_categories:
            categories_text = "\n".join(
                f"- {cat.name}: {cat.description}" for cat in existing_categories
            )
        else:
            categories_text = "(none yet - first batch)"

        samples_text = _format_email_samples(sample_emails, max_emails=batch_size, max_body_length=150)

        prompt_template = load_prompt("refine_folder_structure")
        prompt = prompt_template.format(
            existing_categories=categories_text,
            samples_text=samples_text,
            batch_num=batch_num,
        )

        logger.info(
            f"Refine batch {batch_num}: {len(sample_emails)} emails, "
            f"{len(existing_categories)} existing categories"
        )

        response_text = await self._generate(prompt)

        # Try to parse JSON, with repair fallback
        data = self._parse_json(response_text)

        if data is None:
            json_str = self._extract_json(response_text)
            if json_str:
                logger.info("Attempting JSON repair...")
                repaired = await self.repair_json(json_str)
                if repaired:
                    try:
                        data = json.loads(repaired)
                        logger.info("JSON repair successful")
                    except json.JSONDecodeError:
                        pass

        if isinstance(data, dict):
            try:
                return self._process_refinement_response(data, existing_categories)
            except (KeyError, ValueError) as e:
                logger.warning(f"Failed to parse refinement response: {e}")

        # Fallback: return existing categories unchanged
        return existing_categories, []

    def _process_refinement_response(
        self, data: dict, existing_categories: list[SuggestedFolder]
    ) -> tuple[list[SuggestedFolder], list[dict]]:
        """Process the refinement response data into categories and assignments.

        Args:
            data: Parsed JSON response
            existing_categories: Previous categories to preserve

        Returns:
            Tuple of (categories, assignments)
        """
        assignments = data.get("email_assignments", [])
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

    async def repair_json(self, broken_json: str) -> str | None:
        """Attempt to repair malformed JSON by asking the LLM to fix it.

        Args:
            broken_json: The malformed JSON string

        Returns:
            Repaired JSON string or None if repair failed
        """
        prompt_template = load_prompt("repair_json")
        prompt = prompt_template.format(broken_json=broken_json[:2000])  # Limit size

        response_text = await self._generate(prompt)

        # Try to extract JSON from response
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            json_str = self._extract_json(response_text, start_char, end_char)
            if json_str:
                try:
                    json.loads(json_str)
                    return json_str
                except json.JSONDecodeError:
                    continue
        return None

    async def normalize_categories(
        self,
        categories: list[SuggestedFolder],
    ) -> tuple[list[SuggestedFolder], dict[str, str]]:
        """Consolidate duplicate/overlapping categories.

        Args:
            categories: List of categories to consolidate

        Returns:
            Tuple of (consolidated_categories, rename_map)
        """
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

        data = self._parse_json(response_text)
        if isinstance(data, dict):
            # Build consolidated categories
            for item in data.get("consolidated_categories", []):
                consolidated.append(SuggestedFolder(
                    name=item.get("name", "Unknown"),
                    description=item.get("description", ""),
                    example_criteria=item.get("merged_from", []),
                ))
            rename_map = data.get("rename_map", {})
        else:
            logger.warning("Failed to parse normalization response")
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
        """Ask LLM to complete an incomplete rename map.

        Args:
            original_categories: All original categories before consolidation
            consolidated: Consolidated categories
            partial_map: Incomplete rename map

        Returns:
            Tuple of (consolidated_categories, completed_rename_map)
        """
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

        data = self._parse_json(response_text)
        if isinstance(data, dict):
            new_mappings = data.get("mappings", {})
            for old_name, new_name in new_mappings.items():
                if old_name in missing:
                    partial_map[old_name] = new_name
                    logger.info(f"  Repaired: {old_name} -> {new_name}")
        else:
            logger.warning("Failed to parse repair response")

        return consolidated, partial_map
