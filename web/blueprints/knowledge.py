"""Knowledge-base and knowledge-graph API routes."""

from flask import Blueprint, jsonify, request

knowledge_bp = Blueprint("knowledge", __name__)


# ---------------------------------------------------------------------------
# Lazy helpers – avoid circular imports by deferring to web.app at call time
# ---------------------------------------------------------------------------

def _get_kb():
    from web.knowledge_base import KnowledgeBase
    return KnowledgeBase()


def _get_knowledge_graph():
    from web.app import get_knowledge_graph
    return get_knowledge_graph()


# ======================== Knowledge-Base API ========================


@knowledge_bp.route("/api/knowledge-base/add", methods=["POST"])
def kb_add_document():
    """添加文档到知识库"""
    try:
        data = request.json
        file_path = data.get("file_path")

        if not file_path:
            return jsonify({"success": False, "error": "缺少file_path参数"}), 400

        kb = _get_kb()
        result = kb.add_document(file_path)

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@knowledge_bp.route("/api/knowledge-base/search", methods=["POST"])
def kb_search():
    """搜索知识库"""
    try:
        data = request.json
        query = data.get("query")
        max_results = data.get("max_results", 10)

        if not query:
            return jsonify({"success": False, "error": "缺少query参数"}), 400

        kb = _get_kb()
        results = kb.search(query, max_results=max_results)

        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@knowledge_bp.route("/api/knowledge-base/stats", methods=["GET"])
def kb_stats():
    """获取知识库统计"""
    try:
        kb = _get_kb()
        stats = kb.get_stats()

        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ======================== Knowledge-Graph API ========================


@knowledge_bp.route("/api/knowledge-graph/build", methods=["POST"])
def knowledge_graph_build():
    """构建知识图谱"""
    try:
        data = request.json or {}
        file_paths = data.get("file_paths", [])
        force_rebuild = data.get("force_rebuild", False)

        if not file_paths:
            return jsonify({"error": "缺少文件路径列表"}), 400

        kg = _get_knowledge_graph()
        kg.build_file_graph(file_paths, force_rebuild=force_rebuild)

        stats = kg.get_statistics()

        return jsonify(
            {"success": True, "message": "知识图谱构建完成", "statistics": stats}
        )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@knowledge_bp.route("/api/knowledge-graph/data", methods=["GET"])
def knowledge_graph_data():
    """获取知识图谱数据用于可视化"""
    try:
        max_nodes = request.args.get("max_nodes", 100, type=int)

        kg = _get_knowledge_graph()
        graph_data = kg.get_graph_data(max_nodes=max_nodes)

        return jsonify(graph_data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@knowledge_bp.route("/api/knowledge-graph/neighbors", methods=["POST"])
def knowledge_graph_neighbors():
    """获取文件的邻居节点"""
    try:
        data = request.json or {}
        file_path = data.get("file_path")
        depth = data.get("depth", 1)

        if not file_path:
            return jsonify({"error": "缺少文件路径"}), 400

        kg = _get_knowledge_graph()
        neighbors = kg.get_file_neighbors(file_path, depth=depth)

        return jsonify(neighbors)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@knowledge_bp.route("/api/knowledge-graph/concept-cluster", methods=["POST"])
def knowledge_graph_concept_cluster():
    """获取概念相关的文件集群"""
    try:
        data = request.json or {}
        concept = data.get("concept")
        limit = data.get("limit", 20)

        if not concept:
            return jsonify({"error": "缺少概念参数"}), 400

        kg = _get_knowledge_graph()
        cluster = kg.get_concept_cluster(concept, limit=limit)

        return jsonify(cluster)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@knowledge_bp.route("/api/knowledge-graph/stats", methods=["GET"])
def knowledge_graph_stats():
    """获取知识图谱统计"""
    try:
        kg = _get_knowledge_graph()
        stats = kg.get_statistics()

        return jsonify(stats)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
