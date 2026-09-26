async def test_writer_refreshes_active_collection_after_lock_acquisition():
    from scripts.knowledge import select_writer_vectors
    class State:
        namespace='knowledge'
        async def get(self,key):return {'name':'knowledge_hybrid_v1'}
    class Old:
        client=object()
        collection='knowledge'
    class Service:
        state=State()
        vectors=Old()
    service=Service()
    await select_writer_vectors(service)
    assert service.vectors.collection=='knowledge_hybrid_v1'
