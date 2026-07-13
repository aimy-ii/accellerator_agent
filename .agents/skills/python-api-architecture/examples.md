# Backend API Examples

These examples show the expected code shape. Adapt names and imports to the existing project.

## Model

```python
class StatusType(str, enum.Enum):
    """Статусы сущности."""
    ACTIVE = "active"
    INACTIVE = "inactive"


class Document(Base):
    """
    Модель документа.
    Хранит основные данные и связь с пользователем.
    """

    __tablename__ = "documents"

    name: Mapped[str] = mapped_column(String(DOCUMENT_NAME_LENGTH), nullable=False)
    status: Mapped[StatusType] = mapped_column(
        Enum(StatusType, name="status_type_enum"),
        nullable=False,
        default=StatusType.ACTIVE,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        server_default="true",
    )
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", name="fk_documents_user_id_user"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="documents",
        lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_documents_user_id_name"),
    )

    def __repr__(self) -> str:
        return f"Document(id={self.id}, name={self.name!r})"
```

## Schemas

```python
class DocumentBase(BaseModel):
    """Базовые поля документа, общие для создания и чтения."""

    name: str
    status: str


class DocumentCreate(DocumentBase):
    """Схема создания документа."""

    user_id: Optional[int] = None

    class Config:
        schema_extra = {
            "example": {
                "name": "document",
                "status": "active",
                "user_id": 1,
            }
        }


class DocumentUpdate(BaseModel):
    """
    Обновление полей документа.
    Связанные объекты обновляются через отдельные эндпоинты.
    """

    name: Optional[str] = None
    status: Optional[str] = None

    class Config:
        extra = "forbid"


class DocumentRead(DocumentBase):
    """Схема чтения документа с полными данными."""

    id: int
    is_active: bool
    created_at: datetime

    class Config:
        orm_mode = True
        schema_extra = {
            "example": {
                "id": 1,
                "name": "document",
                "status": "active",
                "is_active": True,
            }
        }
```

## CRUD

```python
class CRUDBase:
    """Базовый CRUD с типовыми операциями для всех моделей."""

    def __init__(self, model) -> None:
        self.model = model

    async def get(self, obj_id: int, session: AsyncSession):
        """Получает объект по id. Возвращает None если не найден."""
        result = await session.execute(
            select(self.model).where(self.model.id == obj_id)
        )
        return result.scalars().first()

    async def get_multi(self, session: AsyncSession) -> list:
        """Возвращает все объекты модели."""
        result = await session.execute(select(self.model))
        return result.scalars().all()

    async def create(self, data: dict, session: AsyncSession):
        """
        Создаёт объект в БД.
        При нарушении уникальности выполняет откат транзакции и возвращает None.
        """
        try:
            db_obj = self.model(**data)
            session.add(db_obj)
            await session.commit()
            await session.refresh(db_obj)
            logging.info(f"Создан объект {self.model.__name__} id={db_obj.id}")
            return db_obj
        except sqlalchemy.exc.IntegrityError:
            await session.rollback()
            return None

    async def update(self, db_obj, obj_in, session: AsyncSession):
        """Обновляет объект из Pydantic-схемы только переданными полями."""
        obj_data = jsonable_encoder(db_obj)
        update_data = obj_in.dict(exclude_unset=True)
        for field in obj_data:
            if field in update_data:
                setattr(db_obj, field, update_data[field])
        session.add(db_obj)
        await session.commit()
        await session.refresh(db_obj)
        return db_obj


class DocumentsCRUD(CRUDBase):
    """CRUD для документов с расширенной логикой фильтрации."""

    async def get_active(self, session: AsyncSession) -> list:
        """Возвращает записи со статусом active."""
        result = await session.execute(
            select(self.model).where(self.model.is_active == True)
        )
        return result.scalars().all()

    async def _apply_filters(self, query, filters):
        """Применяет фильтры к запросу."""
        ...


documents_crud = DocumentsCRUD(model=Document)
```

## Service

```python
async def get_document_data(
    document_id: int,
    db_session: AsyncSession,
    cache: Redis,
) -> dict | None:
    """Возвращает данные документа из кэша или БД."""
    cache_key = f"{DOCUMENT_CACHE_PREFIX}{document_id}"
    cached = await cache.get(cache_key)
    if cached:
        return json.loads(cached)

    document = await documents_crud.get(obj_id=document_id, session=db_session)
    if not document:
        logging.error("[DOCUMENT] Документ не найден document_id=%s", document_id)
        return None

    data = jsonable_encoder(document)
    try:
        await cache.set(cache_key, json.dumps(data), ex=DOCUMENT_CACHE_TTL)
    except Exception as exc:
        logging.warning(
            "[CACHE] Ошибка записи document_id=%s: %s",
            document_id,
            exc,
        )

    return data
```

## Endpoint

```python
documents_router = APIRouter()


@documents_router.post(
    path="/",
    summary="Создание документа",
    response_model=DocumentRead,
    status_code=201,
)
async def create_document(
    data: DocumentCreate,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(current_user),
) -> DocumentRead:
    """Создание нового документа."""
    return await documents_service.create_document(
        data=data,
        session=session,
        user=user,
    )
```

## Task

```python
@broker.task(queue="default", retry_count=3, retry_delay=30.0)
async def validate_document_task(
    document_id: int,
    db_session: AsyncSession = TaskiqDepends(get_async_session),
    cache: Redis = TaskiqDepends(get_cache),
) -> None:
    """Запускает проверку документа в фоне."""
    await run_document_validation_pipeline(
        document_id=document_id,
        db_session=db_session,
        cache=cache,
    )
```

## Unit Test

```python
@pytest.mark.asyncio
async def test_returns_none_when_document_not_found():
    """get_document_data возвращает None, если документ не найден в БД и кэш пуст."""
    with patch(
        "app.services.document_service.main.documents_crud.get",
        new_callable=AsyncMock,
        return_value=None,
    ):
        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)

        result = await get_document_data(1, MagicMock(), cache)

        assert result is None
```
