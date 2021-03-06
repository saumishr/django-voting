from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.contrib.auth.views import redirect_to_login
from django.template import loader, RequestContext
from django.template.loader import render_to_string
from django.utils import simplejson
from django.contrib.comments.models import Comment
from django.utils.translation import ugettext_lazy as _
from django.shortcuts import render_to_response, get_object_or_404
from django.contrib.auth.models import User
from django.conf import settings
from django.core.urlresolvers import reverse

from mezzanine.generic.models import ThreadedComment, Review
from mezzanine.blog.models import BlogPost

from voting.models import Vote
from actstream import action, actions
from actstream.models import Action
from imagestore.models import Album, Image
from userProfile.models import GenericWish, BroadcastWish, BroadcastDeal
from follow.models import Follow

import json

VOTE_DIRECTIONS = (('up', 1), ('down', -1), ('clear', 0))

def vote_on_object(request, model, direction, post_vote_redirect=None,
        object_id=None, slug=None, slug_field=None, template_name=None,
        template_loader=loader, extra_context=None, context_processors=None,
        template_object_name='object', allow_xmlhttprequest=False):
    """
    Generic object vote function.

    The given template will be used to confirm the vote if this view is
    fetched using GET; vote registration will only be performed if this
    view is POSTed.

    If ``allow_xmlhttprequest`` is ``True`` and an XMLHttpRequest is
    detected by examining the ``HTTP_X_REQUESTED_WITH`` header, the
    ``xmlhttp_vote_on_object`` view will be used to process the
    request - this makes it trivial to implement voting via
    XMLHttpRequest with a fallback for users who don't have JavaScript
    enabled.

    Templates:``<app_label>/<model_name>_confirm_vote.html``
    Context:
        object
            The object being voted on.
        direction
            The type of vote which will be registered for the object.
    """
    """
        Voting is only allowed via AJAX. Otherwise raise 404 error.
    """
    if allow_xmlhttprequest and request.is_ajax():
        return xmlhttprequest_vote_on_object(request, model, direction,
                                             object_id=object_id, slug=slug,
                                             slug_field=slug_field)
    else:
        raise Http404()
        
    if extra_context is None: extra_context = {}
    if not request.user.is_authenticated():
        return redirect_to_login(request.path)

    try:
        vote = dict(VOTE_DIRECTIONS)[direction]
    except KeyError:
        raise AttributeError("'%s' is not a valid vote type." % vote_type)

    # Look up the object to be voted on
    lookup_kwargs = {}
    if object_id:
        lookup_kwargs['%s__exact' % model._meta.pk.name] = object_id
    elif slug and slug_field:
        lookup_kwargs['%s__exact' % slug_field] = slug
    else:
        raise AttributeError('Generic vote view must be called with either '
                             'object_id or slug and slug_field.')
    try:
        obj = model._default_manager.get(**lookup_kwargs)
    except ObjectDoesNotExist:
        raise Http404, 'No %s found for %s.' % (model._meta.app_label, lookup_kwargs)

    if request.method == 'POST':
        if post_vote_redirect is not None:
            next = post_vote_redirect
        elif request.REQUEST.has_key('next'):
            next = request.REQUEST['next']
        elif hasattr(obj, 'get_absolute_url'):
            if callable(getattr(obj, 'get_absolute_url')):
                next = obj.get_absolute_url()
            else:
                next = obj.get_absolute_url
        else:
            raise AttributeError('Generic vote view must be called with either '
                                 'post_vote_redirect, a "next" parameter in '
                                 'the request, or the object being voted on '
                                 'must define a get_absolute_url method or '
                                 'property.')

        Vote.objects.record_vote(obj, request.user, vote)
        return HttpResponseRedirect(next)
    else:
        if not template_name:
            template_name = '%s/%s_confirm_vote.html' % (
                model._meta.app_label, model._meta.object_name.lower())
        t = template_loader.get_template(template_name)
        c = RequestContext(request, {
            template_object_name: obj,
            'direction': direction,
        }, context_processors)
        for key, value in extra_context.items():
            if callable(value):
                c[key] = value()
            else:
                c[key] = value
        response = HttpResponse(t.render(c))
        return response

def json_error_response(error_message):
    return HttpResponse(simplejson.dumps(dict(success=False,
                                              error_message=error_message)))

def xmlhttprequest_vote_on_object(request, model, direction,
    object_id=None, slug=None, slug_field=None):
    """
    Generic object vote function for use via XMLHttpRequest.

    Properties of the resulting JSON object:
        success
            ``true`` if the vote was successfully processed, ``false``
            otherwise.
        score
            The object's updated score and number of votes if the vote
            was successfully processed.
        error_message
            Contains an error message if the vote was not successfully
            processed.
    """
    #if request.method == 'GET':
    #    return json_error_response(
    #        'XMLHttpRequest votes can only be made using POST.')
    if not request.user.is_authenticated():
        return json_error_response('Not authenticated.')

    try:
        vote = dict(VOTE_DIRECTIONS)[direction]
    except KeyError:
        return json_error_response(
            '\'%s\' is not a valid vote type.' % direction)

    # Look up the object to be voted on
    lookup_kwargs = {}
    if object_id:
        lookup_kwargs['%s__exact' % model._meta.pk.name] = object_id
    elif slug and slug_field:
        lookup_kwargs['%s__exact' % slug_field] = slug
    else:
        return json_error_response('Generic XMLHttpRequest vote view must be '
                                   'called with either object_id or slug and '
                                   'slug_field.')
    try:
        obj = model._default_manager.get(**lookup_kwargs)
    except ObjectDoesNotExist:
        return json_error_response(
            'No %s found for %s.' % (model._meta.verbose_name, lookup_kwargs))

    # Vote and respond
    if request.method == 'GET':
        return HttpResponse(simplejson.dumps({
            'success': True,
            'score': Vote.objects.get_score(obj),
        }))
    else:
        preVote = Vote.objects.get_score(obj)
        Vote.objects.record_vote(obj, request.user, vote)
        postVote = Vote.objects.get_score(obj)
        if preVote != postVote:
            if vote==1:
                if model.__name__=='Album':
                    action.send(request.user, verb=settings.ALBUM_LIKE_WISH, target=obj, batch_time_minutes=30, is_batchable=True)
                if model.__name__=='ThreadedComment' and isinstance(Comment.objects.get(id=obj.id).content_object, Review):
                    action.send(request.user, verb=settings.REVIEW_COMMENT_LIKE_VERB, action_object=obj, target=Comment.objects.get(id=obj.id).content_object, batch_time_minutes=30, is_batchable=True)
                if model.__name__=='Review' and isinstance(Comment.objects.get(id=obj.id).content_object, BlogPost):
                    action.send(request.user, verb=settings.REVIEW_LIKE_VERB, target=obj,  batch_time_minutes=30, is_batchable=True)
                if model.__name__=='Image':
                    action.send(request.user, verb=settings.PHOTO_LIKE_VERB, target=obj, batch_time_minutes=30, is_batchable=True)
                if model.__name__=='BroadcastWish':
                    action.send(request.user, verb=settings.WISH_LIKE_VERB, target=obj, batch_time_minutes=30, is_batchable=True)
                    actions.follow(request.user, obj, send_action=False, actor_only=False)
                    Follow.objects.get_or_create(request.user, obj) 
                if model.__name__=='BroadcastDeal':
                    action.send(request.user, verb=settings.DEAL_LIKE_VERB, target=obj, batch_time_minutes=30, is_batchable=True)
                    actions.follow(request.user, obj, send_action=False, actor_only=False)
                    Follow.objects.get_or_create(request.user, obj) 
                if model.__name__=='GenericWish':
                    action.send(request.user, verb=settings.POST_LIKE_VERB, target=obj, batch_time_minutes=30, is_batchable=True)
                    actions.follow(request.user, obj, send_action=False, actor_only=False)
                    Follow.objects.get_or_create(request.user, obj)                    
                if model.__name__ == "ThreadedComment" and isinstance(Comment.objects.get(id=obj.id).content_object, Album):
                    action.send(request.user, verb=settings.ALBUM_COMMENT_LIKE_VERB, action_object=obj, target=Comment.objects.get(id=obj.id).content_object, batch_time_minutes=30, is_batchable=True)
                if model.__name__ == "ThreadedComment" and isinstance(Comment.objects.get(id=obj.id).content_object, Image):
                    action.send(request.user, verb=settings.IMAGE_COMMENT_LIKE_VERB, action_object=obj, target=Comment.objects.get(id=obj.id).content_object, batch_time_minutes=30, is_batchable=True)
                if model.__name__ == "ThreadedComment" and type(Comment.objects.get(id=obj.id).content_object) == GenericWish:
                    action.send(request.user, verb=settings.POST_COMMENT_LIKE_VERB, action_object=obj, target=Comment.objects.get(id=obj.id).content_object, batch_time_minutes=30, is_batchable=True) 
                if model.__name__ == "ThreadedComment" and type(Comment.objects.get(id=obj.id).content_object) == BroadcastWish:
                    contentObject = Comment.objects.get(id=obj.id).content_object
                    action.send(request.user, verb=settings.WISH_COMMENT_LIKE_VERB, action_object=obj, target=contentObject, batch_time_minutes=30, is_batchable=True)
                    actions.follow(request.user, contentObject, send_action=False, actor_only=False) 
                    Follow.objects.get_or_create(request.user, contentObject)
                if model.__name__ == "ThreadedComment" and type(Comment.objects.get(id=obj.id).content_object) == BroadcastDeal:
                    contentObject = Comment.objects.get(id=obj.id).content_object
                    action.send(request.user, verb=settings.DEAL_COMMENT_LIKE_VERB, action_object=obj, target=contentObject, batch_time_minutes=30, is_batchable=True)
                    actions.follow(request.user, contentObject, send_action=False, actor_only=False) 
                    Follow.objects.get_or_create(request.user, contentObject)                

                if obj.user and obj.user.is_authenticated():
                    obj.user.num_likes = obj.user.num_likes + 1
                    obj.user.save()
            elif vote==-1 or vote==0:
                if model.__name__=='Album':
                    #action.send(request.user, verb=_('disliked the album'), target=obj)
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.ALBUM_LIKE_WISH, target_content_type=target_content_type, target_object_id = obj.id ).delete() 
                if model.__name__=='ThreadedComment' and isinstance(Comment.objects.get(id=obj.id).content_object, Review):
                    #action.send(request.user, verb=_('disliked the comment on the review'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)   
                    target = Comment.objects.get(id=obj.id).content_object
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(target)
                    action_object_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.REVIEW_COMMENT_LIKE_VERB, action_object_content_type=action_object_content_type, action_object_object_id=obj.id, target_content_type=target_content_type, target_object_id = target.id ).delete()
                if model.__name__=='Review':
                    #action.send(request.user, verb=_('disliked the review on'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)  
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.REVIEW_LIKE_VERB, target_content_type=target_content_type, target_object_id = obj.id ).delete()                 
                if model.__name__=='Image':
                    #action.send(request.user, verb=_('disliked the photo'), target=obj)
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.PHOTO_LIKE_VERB, target_content_type=target_content_type, target_object_id = obj.id ).delete() 
                if model.__name__=='BroadcastWish':
                    #action.send(request.user, verb=_('disliked the photo'), target=obj)
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.WISH_LIKE_VERB, target_content_type=target_content_type, target_object_id = obj.id ).delete() 
                    actions.unfollow(request.user, obj, send_action=False)
                    follow = Follow.objects.get_follows(obj).filter(user=request.user)
                    if follow:
                        follow.delete()                
                if model.__name__=='BroadcastDeal':
                    #action.send(request.user, verb=_('disliked the photo'), target=obj)
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.DEAL_LIKE_VERB, target_content_type=target_content_type, target_object_id = obj.id ).delete()                     
                    actions.unfollow(request.user, obj, send_action=False)
                    follow = Follow.objects.get_follows(obj).filter(user=request.user)
                    if follow:
                        follow.delete()                
                if model.__name__=='GenericWish':
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.POST_LIKE_VERB, target_content_type=target_content_type, target_object_id = obj.id ).delete()                     
                    
                    actions.unfollow(request.user, obj, send_action=False)
                    follow = Follow.objects.get_follows(obj).filter(user=request.user)
                    if follow:
                        follow.delete()

                if model.__name__ == "ThreadedComment" and isinstance(Comment.objects.get(id=obj.id).content_object, Album):
                    #action.send(request.user, verb=_('disliked the comment on the album'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)
                    target = Comment.objects.get(id=obj.id).content_object
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(target)
                    action_object_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.ALBUM_COMMENT_LIKE_VERB, action_object_content_type=action_object_content_type, action_object_object_id=obj.id, target_content_type=target_content_type, target_object_id = target.id ).delete()                 
                if model.__name__ == "ThreadedComment" and isinstance(Comment.objects.get(id=obj.id).content_object, Image):
                    #action.send(request.user, verb=_('disliked the comment on the image'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)
                    target = Comment.objects.get(id=obj.id).content_object
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(target)
                    action_object_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.IMAGE_COMMENT_LIKE_VERB, action_object_content_type=action_object_content_type, action_object_object_id=obj.id, target_content_type=target_content_type, target_object_id = target.id ).delete()                 
                if model.__name__ == "ThreadedComment" and type(Comment.objects.get(id=obj.id).content_object) ==  GenericWish:
                    #action.send(request.user, verb=_('disliked the comment on the image'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)
                    target = Comment.objects.get(id=obj.id).content_object
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(target)
                    action_object_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.POST_COMMENT_LIKE_VERB, action_object_content_type=action_object_content_type, action_object_object_id=obj.id, target_content_type=target_content_type, target_object_id = target.id ).delete() 
                    actions.unfollow(request.user, target, send_action=False)
                    follow = Follow.objects.get_follows(target).filter(user=request.user)
                    if follow:
                        follow.delete()                
                if model.__name__ == "ThreadedComment" and type(Comment.objects.get(id=obj.id).content_object) == BroadcastWish:
                    #action.send(request.user, verb=_('disliked the comment on the image'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)
                    target = Comment.objects.get(id=obj.id).content_object
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(target)
                    action_object_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.WISH_COMMENT_LIKE_VERB, action_object_content_type=action_object_content_type, action_object_object_id=obj.id, target_content_type=target_content_type, target_object_id = target.id ).delete() 
                    actions.unfollow(request.user, target, send_action=False)
                    follow = Follow.objects.get_follows(target).filter(user=request.user)
                    if follow:
                        follow.delete() 
                if model.__name__ == "ThreadedComment" and type(Comment.objects.get(id=obj.id).content_object) == BroadcastDeal:
                    #action.send(request.user, verb=_('disliked the comment on the image'), action_object=obj, target=Comment.objects.get(id=obj.id).content_object)
                    target = Comment.objects.get(id=obj.id).content_object
                    ctype = ContentType.objects.get_for_model(request.user)
                    target_content_type = ContentType.objects.get_for_model(target)
                    action_object_content_type = ContentType.objects.get_for_model(obj)
                    Action.objects.all().filter(actor_content_type=ctype, actor_object_id=request.user.id, verb=settings.DEAL_COMMENT_LIKE_VERB, action_object_content_type=action_object_content_type, action_object_object_id=obj.id, target_content_type=target_content_type, target_object_id = target.id ).delete() 
                    follow = Follow.objects.get_follows(target).filter(user=request.user)
                    if follow:
                        follow.delete() 

                if obj.user and obj.user.is_authenticated() and vote==-1: 
                    obj.user.num_dislikes = obj.user.num_dislikes + 1 
                    obj.user.save()
                if vote==0:
                    if obj.user and obj.user.is_authenticated():
                        obj.user.num_likes = obj.user.num_likes - 1
                        obj.user.save() 

        return HttpResponse(simplejson.dumps({
            'success': True,
            'score': Vote.objects.get_score(obj),
        }))

def get_voters_info(request, content_type_id, object_id):
    ctype = get_object_or_404(ContentType, pk=content_type_id)
    object = get_object_or_404(ctype.model_class(), pk=object_id)
    if request.is_ajax():
        return render_to_response("friend_list_all.html", {
            "friends": Vote.objects.get_voters(object),
        }, context_instance=RequestContext(request))
    else:
        return render_to_response("render_friend_list_all.html", {
            "friends": Vote.objects.get_voters(object),
        }, context_instance=RequestContext(request))

def get_voters_info_inc(request, content_type_id, object_id, sIndex=0, lIndex=0):
    ctype = get_object_or_404(ContentType, pk=content_type_id)
    object = get_object_or_404(ctype.model_class(), pk=object_id)
    
    s = (int)(""+sIndex)
    l = (int)(""+lIndex)
    if s == 0:
        data_href = reverse('get_voters_info_inc', kwargs={ 'content_type_id':content_type_id,
                                                            'object_id':object_id,
                                                            'sIndex':0,
                                                            'lIndex': settings.MIN_VOTERS_CHUNK})
        return render_to_response("friend_list_all.html", {
            "friends": Vote.objects.get_voters_inc(object, s, l),
            'is_incremental': False,
            'data_href':data_href,
            'data_chunk':settings.MIN_VOTERS_CHUNK
        }, context_instance=RequestContext(request))

    sub_voters = Vote.objects.get_voters_inc(object, s, l)

    if request.is_ajax():
        context = RequestContext(request)

        context.update({'friends': sub_voters,
                        'is_incremental': True})

        template = 'friend_list_all.html'
        if sub_voters:
            ret_data = {
                'html': render_to_string(template, context_instance=context).strip(),
                'success': True
            }
        else:
            ret_data = {
                'success': False
            }

        return HttpResponse(json.dumps(ret_data), mimetype="application/json")

    else:
        return render_to_response("render_friend_list_all.html", {
            "friends": sub_voters,
        }, context_instance=RequestContext(request))
