# 本章运行依赖核验

- 项目现有 Python 3.14.4 macOS arm64。
- PyMilvus 2.6.17 dry-run 有完整 wheel 依赖，已在项目 .venv 安装并成功导入 AsyncMilvusClient；未安装 BGE 推理库/权重。
- Milvus 官方 v2.6.17 Compose 引用服务镜像 v2.6.16；采用该官方组合，SDK 2.6.17。
- Milvus v2.6.16 manifest 有 linux/arm64，digest sha256:33a0f81a7343158a1c1e0454b151600074c34f561c069157712b930ca5ca1b04。
- etcd v3.5.25；MinIO RELEASE.2024-05-28T17-19-04Z。
- Docker Hub MinIO 地址拉取返回 denied；改用 MinIO 官方文档的 quay.io/minio/minio 同版本，未更换存储产品。
- 官方参考：https://raw.githubusercontent.com/milvus-io/milvus/v2.6.17/deployments/docker/standalone/docker-compose.yml
- 官方 MinIO 文档：https://github.com/minio/minio/blob/master/docs/docker/README.md
- 同类故障：https://github.com/milvus-io/milvus/issues/53430
- MinIO 所选镜像没有 curl，健康检查实测改为镜像自带 `mc ready local` 成功。
- Milvus、etcd、MinIO、原MySQL均 healthy；随机测试集合完成显式id重复upsert、Strong搜索、删除，1项真实集成测试通过。
