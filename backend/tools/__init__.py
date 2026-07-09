"""
  PDF Loader 只负责把PDF解析成PaperDocument，不做chunk，embedding、RAG、Agent分析

  chunker.py: 负责把pdf页面切成PaperChunk，为后面的embedding/Retriever/RAG做准备

  embedder: 接收文本生成向量表示，paperchunk -> embedder -> chunk_id -> embedding vector

  vector_store: 负责保存向量并做相似度检索 embeddingrecord -> vectorstore -> top-k similar chunk_id + score

  retriever: 把前面完成的模块串起来：接受paperchunk列表 -> 调用embedder生成chunk embeddings -> 把embeddings 加入vectorstore -> 
              接收query -> 调用embedder.embed)query() -> 调用 vector_store.search() -> 根据chunk_id找回原始paperchunk -> 返回evidenceBundle
"""