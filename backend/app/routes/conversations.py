"""Conversation CRUD endpoints — migrated from sub-project SQLite to Supabase."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import conversation_store

router = APIRouter(prefix="/conversations", tags=["conversations"])


class CreateConversationRequest(BaseModel):
    user_id: str
    title: str = "New Chat"


class ConversationTitleRequest(BaseModel):
    user_id: str
    title: str


class ConversationPinRequest(BaseModel):
    user_id: str
    pinned: bool


@router.post("")
def create_conversation(req: CreateConversationRequest) -> dict:
    conv = conversation_store.create_conversation(req.user_id, req.title)
    return {"status": "ok", "conversation": conv}


@router.get("")
def list_conversations(user_id: str) -> dict:
    convs = conversation_store.list_conversations(user_id)
    return {"status": "ok", "conversations": convs}


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str) -> dict:
    conv = conversation_store.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    history = conversation_store.get_conversation_history(conversation_id)
    return {"status": "ok", "conversation": conv, "messages": history}


@router.patch("/{conversation_id}")
def rename_conversation(conversation_id: str, req: ConversationTitleRequest) -> dict:
    conversation_store.rename_conversation(conversation_id, req.title)
    return {"status": "ok"}


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str) -> dict:
    conversation_store.delete_conversation(conversation_id)
    return {"status": "ok"}


@router.post("/{conversation_id}/pin")
def pin_conversation(conversation_id: str, req: ConversationPinRequest) -> dict:
    conversation_store.pin_conversation(conversation_id, req.pinned)
    return {"status": "ok"}
