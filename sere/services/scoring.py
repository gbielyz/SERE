def class_letter(points):
    for threshold, value in [
        (96, "A+"),
        (86, "A"),
        (81, "A-"),
        (76, "B+"),
        (66, "B"),
        (61, "B-"),
        (56, "C+"),
        (46, "C"),
        (41, "C-"),
        (36, "D+"),
        (26, "D"),
        (21, "D-"),
        (16, "E+"),
        (6, "E"),
        (1, "E-"),
    ]:
        if points >= threshold:
            return value
    return "F"


def concept(media):
    if media >= 90:
        return "A"
    if media >= 80:
        return "B"
    if media >= 70:
        return "C"
    if media >= 60:
        return "D"
    return "E"


def overall_score(student):
    score = (
        float(student["academico"])
        + float(student["adaptabilidade"])
        + float(student["fisico"])
        + float(student["social"]) * 0.5
    ) / 3.5
    return round(score, 1)


def overall_with_concept(student):
    score = overall_score(student)
    return score, concept(score)
