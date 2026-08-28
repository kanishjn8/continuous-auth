"""Required baseline comparisons (PLAN.md Section 10.4).

Isolation Forest must be compared against at least one simple statistical
baseline (Mahalanobis distance to centroid, ``mahalanobis.py``) and at
least one alternative one-class model (One-Class SVM, ``alt_one_class.py``)
-- otherwise "Isolation Forest achieved FAR=x%, FRR=y%" is uninterpretable.
Both are trained through the exact same ``ml/training/common.py`` machinery
(identical preprocessing, identical calibration) as the primary model, so
comparisons are apples-to-apples.
"""
