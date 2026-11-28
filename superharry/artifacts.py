from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .models import ArtifactGroup, Conversation, SkillArtifact


@never_cache
@login_required
@require_GET
def conversation_files(request, conversation_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    groups = list(ArtifactGroup.objects.filter(conversation=conversation, owner=request.user).prefetch_related('artifacts__run').order_by('name', 'pk'))
    return render(request, 'conversation_files.html', {'conversation': conversation, 'groups': groups})


@never_cache
@login_required
@require_POST
def move_artifact(request, conversation_id, artifact_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    with transaction.atomic():
        artifact = get_object_or_404(SkillArtifact, pk=artifact_id, run__owner=request.user, run__reply__conversation=conversation)
        previous = artifact.group
        selected = request.POST.get('group', '')
        if selected == 'new':
            group = ArtifactGroup.objects.create(owner=request.user, conversation=conversation, name=artifact.name)
        elif selected.isdigit():
            group = get_object_or_404(ArtifactGroup, pk=int(selected), owner=request.user, conversation=conversation)
        else:
            return HttpResponse('Choose a group.', status=400)
        artifact.group = group
        artifact.save(update_fields=('group',))
        if previous and previous.pk != group.pk and not previous.artifacts.exists():
            previous.delete()
    return redirect('conversation-files', conversation_id=conversation.pk)


@never_cache
@login_required
@require_GET
def delete_group(request, conversation_id, group_id):
    conversation = get_object_or_404(Conversation, pk=conversation_id, owner=request.user)
    group = get_object_or_404(ArtifactGroup, pk=group_id, owner=request.user, conversation=conversation)
    first = group.artifacts.order_by('pk').first()
    if first is None:
        return redirect('conversation-files', conversation_id=conversation.pk)
    return redirect(f"{reverse('file-delete')}?kind=artifact&id={first.pk}")
