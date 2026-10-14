from pydantic import BaseModel, ConfigDict, Field


class StoreCreate(BaseModel):
    name: str
    website: str | None = None
    country: str = "PT"
    selectors: dict = Field(default_factory=dict)


class ProductCreate(BaseModel):
    name: str
    set_name: str | None = None
    card_number: str | None = None
    image_url: str | None = None


class TrackingCreate(BaseModel):
    product_id: int
    store_id: int
    product_url: str
    is_active: bool = True
    is_in_stock: bool = True


class NotificationChannelCreate(BaseModel):
    name: str
    channel_type: str
    target: str
    enabled: bool = True


class StoreOut(BaseModel):
    id: int
    name: str
    website: str | None = None
    country: str
    selectors: dict

    model_config = ConfigDict(from_attributes=True)


class ProductOut(BaseModel):
    id: int
    name: str
    set_name: str | None = None
    card_number: str | None = None
    image_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class TrackingOut(BaseModel):
    id: int
    product_id: int
    store_id: int
    product_url: str
    is_active: bool
    last_seen_price: float | None = None
    last_seen_promo: bool = False
    is_in_stock: bool = True

    model_config = ConfigDict(from_attributes=True)
