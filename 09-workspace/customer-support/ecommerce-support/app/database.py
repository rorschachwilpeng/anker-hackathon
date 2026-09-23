from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


class Database:
    def __init__(self, url: str):
        if not url.startswith('mysql+aiomysql://'):
            raise ValueError('DATABASE_URL must use mysql+aiomysql')
        self.engine = create_async_engine(url, pool_pre_ping=True, pool_recycle=1800)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self):
        await self.engine.dispose()
