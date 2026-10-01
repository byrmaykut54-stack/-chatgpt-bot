def build_proposal(job, profile):
    title = job.get("title", "this project")
    skills = ", ".join(job.get("skills", [])[:4])
    parts = ["Hi, I can help with " + title.lower() + ".", "I noticed the project specifically involves " + (skills or "the requested workflow") + "."]
    if profile.get("relevant_experience"): parts.append("Relevant experience: " + profile["relevant_experience"])
    if profile.get("proof"): parts.append("Relevant proof: " + profile["proof"])
    parts.append("I would first confirm the current workflow and required output, then implement and test the solution. I can keep the implementation simple and documented.")
    parts.append("If useful, I can start with a small first milestone and demonstrate the working result.")
    parts.append("Best regards")
    return "\n\n".join(parts)
