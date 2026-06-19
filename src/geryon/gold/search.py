from pathlib import Path
from typing import Any
import yaml  # type: ignore[import-untyped]

from geryon.domain.models import SearchHit, SearchFilter, DictionaryEntry, UserProfile
from geryon.search.retriever import Retriever

class SearchResultList(list[SearchHit]):
    """Subclass of list to dynamically carry facets metadata without breaking list contract."""
    facets: dict[str, dict[str, int]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.facets = {}

class GoldSearch:
    """Gold Layer Personalized Dictionary Search.
    Handles query expansion using dictionary entries and personalized re-ranking
    based on user profiles.
    """
    retriever: Retriever
    dictionary: list[DictionaryEntry]
    profiles: dict[str, UserProfile]

    def __init__(
        self,
        retriever: Retriever,
        dictionary: list[DictionaryEntry] | None = None,
        profiles: dict[str, UserProfile] | None = None
    ) -> None:
        self.retriever = retriever
        
        if dictionary is None:
            self.dictionary = self._load_dictionary()
        else:
            self.dictionary = dictionary

        if profiles is None:
            self.profiles = self._load_profiles()
        else:
            self.profiles = profiles

    def _load_dictionary(self) -> list[DictionaryEntry]:
        # 수동(dictionary.yaml) + 자동(dictionary.auto.yaml) 분리 관리.
        # 같은 term 이면 먼저 로드한 수동을 우선(자동은 보강만). [dict_bootstrap]
        # 자동 사전은 기본 OFF(opt-in): 데이터에 따라 raw 자동사전이 정확도를 떨어뜨릴 수
        # 있어, GERYON_DICT_AUTO=1 일 때만 로드한다(수동 사전은 항상 로드).
        from geryon.config import DICT_AUTO_ENABLED
        base = Path.home() / ".geryon" / "gold"
        entries: list[DictionaryEntry] = []
        seen: set[str] = set()
        files = ("dictionary.yaml", "dictionary.auto.yaml") if DICT_AUTO_ENABLED else ("dictionary.yaml",)
        for fname in files:  # 수동 먼저 = 우선
            path = base / fname
            if not path.exists():
                continue
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for item in data:
                if not isinstance(item, dict) or "term" not in item:
                    continue
                if item["term"] in seen:  # 수동 우선 — 같은 term 이면 자동 건너뜀
                    continue
                seen.add(item["term"])
                # dict_bootstrap 의 보조 필드(_freq·_todo 등) 제거 후 생성
                clean = {k: v for k, v in item.items() if not k.startswith("_")}
                try:
                    entries.append(DictionaryEntry(**clean))
                except Exception:
                    pass
        return entries

    def _load_profiles(self) -> dict[str, UserProfile]:
        profiles_dir = Path.home() / ".geryon" / "gold" / "profiles"
        if not profiles_dir.exists():
            return {}
        profiles = {}
        try:
            for file_path in profiles_dir.glob("*.yaml"):
                user_id = file_path.stem
                with open(file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if isinstance(data, dict):
                    if "user_id" not in data:
                        data["user_id"] = user_id
                    profiles[user_id] = UserProfile(**data)
        except Exception:
            pass
        return profiles

    def _load_user_dictionary(self, user_id: str) -> list[DictionaryEntry]:
        path = Path.home() / ".geryon" / "gold" / "users" / f"{user_id}.dictionary.yaml"
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if not isinstance(data, list):
                return []
            entries = []
            for item in data:
                if isinstance(item, dict):
                    entries.append(DictionaryEntry(**item))
            return entries
        except Exception:
            return []

    def search(
        self,
        query: str,
        k: int = 10,
        user_id: str | None = None,
        filters: SearchFilter | None = None,
        offset: int = 0,
    ) -> list[SearchHit]:
        """Performs dictionary expansion, retrieves candidates from Silver retriever,
        applies personalization boosting, and reranks results.
        
        Complies with the Search Relevance Gate. Low-relevance results are filtered out
        by the Silver retriever, shielding the Gold search layer.
        """
        dict_entries = list(self.dictionary)
        if user_id:
            user_dict = self._load_user_dictionary(user_id)
            user_terms = {entry.term.lower() for entry in user_dict}
            dict_entries = [entry for entry in dict_entries if entry.term.lower() not in user_terms]
            dict_entries.extend(user_dict)

        tokens = query.split()
        expanded_terms = [query]
        matched_entries = []

        limit_count = 10
        seen_expanded = {query.lower()}

        for token in tokens:
            for entry in dict_entries:
                is_match = False
                if entry.term.lower() == token.lower():
                    is_match = True
                elif any(syn.lower() == token.lower() for syn in entry.synonyms):
                    is_match = True

                if is_match:
                    if entry not in matched_entries:
                        matched_entries.append(entry)
                    
                    term_lower = entry.term.lower()
                    if term_lower not in seen_expanded and len(seen_expanded) < limit_count:
                        seen_expanded.add(term_lower)
                        expanded_terms.append(entry.term)

                    for syn in entry.synonyms:
                        syn_lower = syn.lower()
                        if syn_lower not in seen_expanded and len(seen_expanded) < limit_count:
                            seen_expanded.add(syn_lower)
                            expanded_terms.append(syn)

        if len(expanded_terms) > 1:
            seen = set()
            unique_terms = []
            for t in expanded_terms:
                t_lower = t.lower()
                if t_lower not in seen:
                    seen.add(t_lower)
                    unique_terms.append(t)
            search_query = " ".join(unique_terms)
        else:
            search_query = query

        # Step 2: Silver hybrid search (k * 2 candidate retrieval)
        hits = self.retriever.search(search_query, k=(k + offset) * 2, offset=0, filters=filters)
        if not hits:
            return []

        # Graceful fallback if no user_id, no profiles loaded, or profile not found
        if not user_id or not self.profiles or user_id not in self.profiles:
            res = SearchResultList(hits[offset:offset+k])
            if hasattr(hits, "facets"):
                res.facets = getattr(hits, "facets")
            return res

        profile = self.profiles[user_id]
        repository = getattr(self.retriever, "repository", None)

        # Step 3: Personalization Reranking and Boosting
        boosted_hits = []
        for hit in hits:
            boost = 1.0

            # Space/Repo preference boost
            if profile.preferred_spaces and hit.space_or_repo in profile.preferred_spaces:
                boost *= 1.2

            # Fetch full document for tags and content match if repository exists
            doc = None
            if repository:
                try:
                    doc = repository.get(hit.doc_id)
                except Exception:
                    pass

            # Tag preference boost
            if doc and profile.preferred_tags and doc.tags:
                if any(t in profile.preferred_tags for t in doc.tags):
                    boost *= 1.3

            # Dictionary term match boost
            for entry in matched_entries:
                if doc:
                    doc_text = (doc.title + " " + doc.body_markdown).lower()
                    terms_to_check = [entry.term] + entry.synonyms
                    if any(t.lower() in doc_text for t in terms_to_check):
                        boost *= entry.boost
                else:
                    hit_text = (hit.title + " " + hit.snippet).lower()
                    terms_to_check = [entry.term] + entry.synonyms
                    if any(t.lower() in hit_text for t in terms_to_check):
                        boost *= entry.boost

            # Apply final score multiplier
            hit.score *= boost
            boosted_hits.append(hit)

        # Step 4: Re-sort & Limit
        boosted_hits.sort(key=lambda h: h.score, reverse=True)
        res = SearchResultList(boosted_hits[offset:offset+k])
        if hasattr(hits, "facets"):
            res.facets = getattr(hits, "facets")
        return res
